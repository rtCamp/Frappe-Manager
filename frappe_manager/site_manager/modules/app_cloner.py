"""
AppCloner - Parallel Git Cloning Module

This module handles parallel cloning of Frappe apps with multi-auth fallback.
Runs on the host machine (not in container) to access SSH keys and avoid Docker overhead.

Key Features:
- Parallel cloning using ThreadPoolExecutor (2-3x faster)
- Smart auth prioritization: Token first when provided, HTTPS fallback
- Support for subdirectory apps (monorepos)
- Shallow clones for speed (--depth 1)
- Repository validation before cloning
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from git import GitCommandError, Repo  # type: ignore

from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.logger import ctx_submit, get_logger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.site_manager.bench_config import AppConfig, extract_app_python_module_name


class AppClonerError(FrappeManagerException):
    """Custom exception for app cloning errors."""


class AppCloner:
    """
    Handles parallel Git cloning with authentication fallback.

    Runs on HOST MACHINE (not in container) to:
    - Access SSH keys (~/.ssh/)
    - Avoid Docker overhead
    - Enable parallel operations

    Authentication priority:
    1. HTTPS (public repos)
    2. GitHub token (private repos with --github-token)
    3. SSH (private repos with SSH keys configured)
    """

    def __init__(
        self,
        apps_dir: Path,
        github_token: str | None = None,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize AppCloner.

        Args:
            apps_dir: Path to bench apps directory (e.g., /benches/mybench/workspace/frappe-bench/apps)
            github_token: Optional GitHub personal access token for private repos
            output_handler: Optional output handler for progress updates
        """
        self.logger = get_logger(component="app_cloner")
        self.apps_dir = Path(apps_dir)
        self.github_token = github_token
        self.output = output_handler

        self.apps_dir.mkdir(parents=True, exist_ok=True)

    def clone_apps_parallel(self, apps: list[AppConfig], max_workers: int = 5) -> dict[str, Path]:
        """
        Clone multiple apps in parallel.

        Optimizes for monorepos: If multiple apps come from the same repo+ref with
        subdirectories, the monorepo is cloned once and shared.

        Args:
            apps: List of AppConfig objects to clone
            max_workers: Maximum number of parallel workers (default: 5)

        Returns:
            Dict mapping app_name to clone_path

        Raises:
            AppClonerError: If any clone operation fails
        """
        if not apps:
            return {}

        self.logger.info(f"Starting parallel clone of {len(apps)} apps")

        # Group apps by monorepo (same repo+ref with subdirs)
        monorepo_groups = {}
        standalone_apps = []

        for app in apps:
            if app.subdir_path:
                # This is a monorepo app - group by repo+ref
                key = f"{app.repo}:{app.ref or 'default'}"
                if key not in monorepo_groups:
                    monorepo_groups[key] = []
                monorepo_groups[key].append(app)
            else:
                # Standalone app
                standalone_apps.append(app)

        cloned_apps = {}
        failed_apps = []

        # Clone standalone apps in parallel
        if standalone_apps:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_app = {ctx_submit(executor, self._clone_app, app): app for app in standalone_apps}

                for future in as_completed(future_to_app):
                    app = future_to_app[future]
                    try:
                        app_name, clone_path = future.result()
                        cloned_apps[app_name] = clone_path
                        if self.output:
                            self.output.print(f"Cloned {app_name}", emoji_code=":white_check_mark:")
                        self.logger.info(f"Successfully cloned {app_name} to {clone_path}")
                    except Exception as e:
                        failed_apps.append((app.name, str(e)))
                        self.logger.error(f"Failed to clone {app.name}: {e}")

        # Handle monorepo groups (sequential per group, but parallel within group extraction)
        for repo_key, group_apps in monorepo_groups.items():
            try:
                self.logger.info(f"Processing monorepo {repo_key} with {len(group_apps)} apps")
                monorepo_results = self._clone_monorepo_apps(group_apps)
                cloned_apps.update(monorepo_results)
            except Exception as e:
                for app in group_apps:
                    failed_apps.append((app.name, str(e)))
                    self.logger.error(f"Failed to clone {app.name} from monorepo: {e}")

        # Raise exception if any clones failed
        if failed_apps:
            error_msg = "Failed to clone apps:\n" + "\n".join(f"  - {name}: {error}" for name, error in failed_apps)
            raise AppClonerError(error_msg)

        return cloned_apps

    def _clone_monorepo_apps(self, apps: list[AppConfig]) -> dict[str, Path]:
        """
        Clone multiple apps from the same monorepo efficiently.

        Strategy:
        1. Clone the monorepo once to a temporary location
        2. Extract each subdirectory to its respective app directory
        3. Clean up the shared monorepo clone

        Args:
            apps: List of AppConfig objects from the same repo

        Returns:
            Dict mapping app_name to clone_path
        """
        import shutil

        if not apps:
            return {}

        # Use the first app's config for cloning (they all share repo+ref)
        first_app = apps[0]
        repo_name = first_app.repo.replace("/", "_")

        # Clone monorepo to a temporary shared location
        shared_clone_path = self.apps_dir / f".tmp_monorepo_{repo_name}"

        if shared_clone_path.exists():
            shutil.rmtree(shared_clone_path)

        self.logger.info(f"Cloning shared monorepo {first_app.repo} to {shared_clone_path}")

        # Get auth methods and clone
        auth_methods = self._get_auth_methods(first_app)
        cloned = False

        for method_name, repo_url in auth_methods:
            try:
                self.logger.debug(f"Trying {method_name} for monorepo {first_app.repo}")
                self._git_clone(repo_url, shared_clone_path, first_app)
                self.logger.info(f"Successfully cloned monorepo using {method_name}")
                cloned = True
                break
            except Exception as e:
                self.logger.debug(f"{method_name} failed for monorepo: {e}")
                if shared_clone_path.exists():
                    shutil.rmtree(shared_clone_path)
                continue

        if not cloned:
            raise Exception(f"Failed to clone monorepo {first_app.repo}")

        # Extract each app's subdirectory
        result = {}

        for app in apps:
            try:
                # First, use the subdirectory name as temporary location
                temp_app_path = self.apps_dir / app.name

                if temp_app_path.exists():
                    self.logger.info(f"App {app.name} already exists, skipping")
                    result[app.name] = temp_app_path
                    continue

                subdir_path = shared_clone_path / (app.subdir_path or "")

                if not subdir_path.exists():
                    raise Exception(
                        f"Subdirectory '{app.subdir_path}' not found in monorepo. "
                        f"Available: {[d.name for d in shared_clone_path.iterdir() if d.is_dir() and not d.name.startswith('.')]}",
                    )

                # Copy subdirectory to temporary location
                self.logger.info(f"Extracting {app.name} from {app.subdir_path}")
                shutil.copytree(subdir_path, temp_app_path, symlinks=True)

                # Extract actual Python module name from pyproject.toml or hooks.py
                actual_app_name = extract_app_python_module_name(temp_app_path)

                # If the actual app name differs from directory name, rename
                if actual_app_name != app.name:
                    final_app_path = self.apps_dir / actual_app_name
                    if final_app_path.exists():
                        # Target already exists - should not happen, but handle it
                        self.logger.warning(
                            f"App directory {actual_app_name} already exists. Using subdirectory name instead.",
                        )
                        actual_app_name = app.name
                        final_app_path = temp_app_path
                    else:
                        self.logger.info(
                            f"Renaming app directory from '{app.name}' to '{actual_app_name}' (Python module name)",
                        )
                        shutil.move(str(temp_app_path), str(final_app_path))
                else:
                    final_app_path = temp_app_path

                # Update the AppConfig with the correct name
                app.name = actual_app_name
                result[actual_app_name] = final_app_path

                if self.output:
                    self.output.print(f"Extracted {actual_app_name}", emoji_code=":white_check_mark:")

            except Exception as e:
                self.logger.error(f"Failed to extract {app.name}: {e}")
                raise

        # Clean up shared monorepo
        if shared_clone_path.exists():
            self.logger.debug(f"Cleaning up shared monorepo at {shared_clone_path}")
            shutil.rmtree(shared_clone_path)

        return result

    def _clone_app(self, app: AppConfig) -> tuple[str, Path]:
        """
        Clone a single standalone app (non-subdirectory) with authentication fallback.

        NOTE: This method should ONLY be called for standalone apps (app.subdir_path is None).
        Subdirectory apps are handled by _clone_monorepo_apps() for efficiency.

        If app.repo_url is set (by validate_repos_exist), it will be used directly
        without trying other authentication methods. This avoids redundant auth attempts.

        Args:
            app: AppConfig object with repo details (must NOT have subdir_path)

        Returns:
            Tuple of (app_name, clone_path)

        Raises:
            Exception: If all authentication methods fail
        """
        if app.subdir_path:
            raise ValueError(
                f"_clone_app() called with subdirectory app {app.name}. "
                "Subdirectory apps must be handled by _clone_monorepo_apps().",
            )

        clone_path = self.apps_dir / app.name

        # Skip if already cloned
        if clone_path.exists():
            self.logger.info(f"App {app.name} already exists at {clone_path}, skipping")
            return (app.name, clone_path)

        # Get authentication methods to try
        # If app.repo_url is set (by validation), it will be tried first
        # Otherwise, tries HTTPS → Token → SSH in order
        auth_methods = self._get_auth_methods(app)
        last_error = None

        for method_name, repo_url in auth_methods:
            try:
                self.logger.debug(f"Trying {method_name} for {app.name}: {repo_url}")
                self._git_clone(repo_url, clone_path, app)
                self.logger.info(f"Successfully cloned {app.name} using {method_name}")

                # Detect actual Python module name from pyproject.toml or hooks.py
                actual_app_name = extract_app_python_module_name(clone_path)

                # Rename directory if module name differs from initial name
                if actual_app_name != app.name:
                    final_path = self.apps_dir / actual_app_name
                    if final_path.exists():
                        # Target already exists - should not happen, but handle it
                        self.logger.warning(f"App directory {actual_app_name} already exists. Using repo name instead.")
                        actual_app_name = app.name
                        final_path = clone_path
                    else:
                        self.logger.info(
                            f"Renaming app directory from '{app.name}' to '{actual_app_name}' (Python module name)",
                        )
                        import shutil

                        shutil.move(str(clone_path), str(final_path))
                        clone_path = final_path

                # Update the AppConfig with correct name
                app.name = actual_app_name

                return (actual_app_name, clone_path)

            except (GitCommandError, Exception) as e:
                last_error = e
                self.logger.debug(f"{method_name} failed for {app.name}: {e}")
                # Clean up failed clone attempt
                if clone_path.exists():
                    import shutil

                    shutil.rmtree(clone_path)
                continue

        # All methods failed
        raise Exception(
            f"Failed to clone {app.name} from {app.repo}. Tried all authentication methods. Last error: {last_error}",
        )

    def _get_auth_methods(self, app: AppConfig) -> list[tuple[str, str]]:
        """
        Get list of authentication methods to try in order.

        Delegates to AppConfig.get_auth_methods() for consistency with validation.
        Priority order:
        - With token: Token → HTTPS → SSH
        - Without token: HTTPS → SSH

        Returns:
            List of (method_name, repo_url) tuples
        """
        return app.get_auth_methods(github_token=self.github_token)

    def _git_clone(self, repo_url: str, clone_path: Path, app: AppConfig) -> None:
        """
        Execute git clone operation.

        Args:
            repo_url: Git repository URL
            clone_path: Destination path for clone
            app: AppConfig object with clone options
        """
        clone_kwargs = {
            "branch": app.ref if app.ref and not app.is_commit else None,
            "depth": 1 if app.shallow_clone and not app.is_commit else None,
        }

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"
        clone_kwargs["env"] = env

        clone_kwargs = {k: v for k, v in clone_kwargs.items() if v is not None}

        clone_type = "shallow" if clone_kwargs.get("depth") == 1 else "full"
        ref_info = f" (ref: {app.ref})" if app.ref else ""
        self.logger.debug(f"Cloning {app.name} from {repo_url}{ref_info} [{clone_type} clone]")

        repo = Repo.clone_from(repo_url, clone_path, **clone_kwargs)

        if app.is_commit:
            self.logger.debug(f"Checking out commit {app.ref} for {app.name}")
            repo.git.checkout(app.ref)

    @staticmethod
    def validate_repos_exist(apps: list[AppConfig], github_token: str | None = None) -> tuple[bool, list[str]]:
        """
        Validate that all app repositories exist before attempting to clone.

        DEPRECATED: This method now delegates to AppConfig.validate_repos_batch().
        New code should call AppConfig.validate_repos_batch() directly.

        Args:
            apps: List of AppConfig objects to validate (modified in-place)
            github_token: Optional GitHub token for private repos

        Returns:
            Tuple of (all_valid: bool, messages: List[str])
            Messages include both success (✓) and error (❌) messages with auth method details
        """
        result = AppConfig.validate_repos_batch(apps, github_token)
        return (result.all_valid, result.messages)
