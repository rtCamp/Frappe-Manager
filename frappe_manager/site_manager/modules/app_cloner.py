"""
AppCloner - Parallel Git Cloning Module

This module handles parallel cloning of Frappe apps with multi-auth fallback.
Runs on the host machine (not in container) to access SSH keys and avoid Docker overhead.

Key Features:
- Parallel cloning using ThreadPoolExecutor (2-3x faster)
- Multi-auth fallback: HTTPS → GitHub Token → SSH
- Support for subdirectory apps (monorepos)
- Shallow clones for speed (--depth 1)
- Repository validation before cloning
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from git import Repo, GitCommandError  # type: ignore

from frappe_manager.output_manager import OutputHandler
from frappe_manager.site_manager.bench_config import AppConfig, extract_app_python_module_name
from frappe_manager.logger import log


class AppClonerError(Exception):
    """Custom exception for app cloning errors."""

    pass


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
        github_token: Optional[str] = None,
        output_handler: Optional[OutputHandler] = None,
    ):
        """
        Initialize AppCloner.

        Args:
            apps_dir: Path to bench apps directory (e.g., /benches/mybench/workspace/frappe-bench/apps)
            github_token: Optional GitHub personal access token for private repos
            output_handler: Optional output handler for progress updates
        """
        self.apps_dir = Path(apps_dir)
        self.github_token = github_token
        self.output = output_handler
        self.logger = log.get_logger()

        # Ensure apps directory exists
        self.apps_dir.mkdir(parents=True, exist_ok=True)

    def clone_apps_parallel(self, apps: List[AppConfig], max_workers: int = 5) -> Dict[str, Path]:
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
                future_to_app = {executor.submit(self._clone_app, app): app for app in standalone_apps}

                for future in as_completed(future_to_app):
                    app = future_to_app[future]
                    try:
                        app_name, clone_path = future.result()
                        cloned_apps[app_name] = clone_path
                        if self.output:
                            self.output.print(f"✓ Cloned {app_name}")
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

    def _clone_monorepo_apps(self, apps: List[AppConfig]) -> Dict[str, Path]:
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
                        f"Available: {[d.name for d in shared_clone_path.iterdir() if d.is_dir() and not d.name.startswith('.')]}"
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
                            f"App directory {actual_app_name} already exists. Using subdirectory name instead."
                        )
                        actual_app_name = app.name
                        final_app_path = temp_app_path
                    else:
                        self.logger.info(
                            f"Renaming app directory from '{app.name}' to '{actual_app_name}' (Python module name)"
                        )
                        shutil.move(str(temp_app_path), str(final_app_path))
                else:
                    final_app_path = temp_app_path

                # Update the AppConfig with the correct name
                app.name = actual_app_name
                result[actual_app_name] = final_app_path

                if self.output:
                    self.output.print(f"✓ Extracted {actual_app_name}")

            except Exception as e:
                self.logger.error(f"Failed to extract {app.name}: {e}")
                raise

        # Clean up shared monorepo
        if shared_clone_path.exists():
            self.logger.debug(f"Cleaning up shared monorepo at {shared_clone_path}")
            shutil.rmtree(shared_clone_path)

        return result

    def _clone_app(self, app: AppConfig) -> Tuple[str, Path]:
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
                "Subdirectory apps must be handled by _clone_monorepo_apps()."
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
                            f"Renaming app directory from '{app.name}' to '{actual_app_name}' (Python module name)"
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
            f"Failed to clone {app.name} from {app.repo}. Tried all authentication methods. Last error: {last_error}"
        )

    def _get_auth_methods(self, app: AppConfig) -> List[Tuple[str, str]]:
        """
        Get list of authentication methods to try in order.

        Returns:
            List of (method_name, repo_url) tuples
        """
        methods = []

        # If custom repo_url provided, use it
        if app.repo_url:
            methods.append(("Custom URL", app.repo_url))
            return methods

        # 1. HTTPS (public repos)
        https_url = f"https://github.com/{app.repo}.git"
        methods.append(("HTTPS", https_url))

        # 2. HTTPS with GitHub token (private repos)
        if self.github_token:
            token_url = f"https://{self.github_token}@github.com/{app.repo}.git"
            methods.append(("GitHub Token", token_url))

        # 3. SSH (private repos with SSH keys)
        ssh_url = f"git@github.com:{app.repo}.git"
        methods.append(("SSH", ssh_url))

        return methods

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

        # Remove None values
        clone_kwargs = {k: v for k, v in clone_kwargs.items() if v is not None}

        # Clone the repository
        repo = Repo.clone_from(repo_url, clone_path, **clone_kwargs)

        # If specific commit, checkout after clone
        if app.is_commit:
            repo.git.checkout(app.ref)

    @staticmethod
    def validate_repos_exist(apps: List[AppConfig], github_token: Optional[str] = None) -> Tuple[bool, List[str]]:
        """
        Validate that all app repositories exist before attempting to clone.

        This performs a lightweight check using 'git ls-remote' to verify repos
        are accessible without actually cloning them. Should be called BEFORE
        creating any infrastructure (directories, containers, etc.).

        Uses the same authentication fallback as cloning:
        1. HTTPS (public repos)
        2. HTTPS with token (private repos with --github-token)
        3. SSH (private repos with SSH keys)

        IMPORTANT: This method modifies the AppConfig objects in-place by setting
        the validated repo_url. This allows cloning to skip auth fallback attempts
        and directly use the working URL.

        Args:
            apps: List of AppConfig objects to validate (modified in-place)
            github_token: Optional GitHub token for private repos

        Returns:
            Tuple of (all_valid: bool, error_messages: List[str])

        Example:
            >>> valid, errors = AppCloner.validate_repos_exist(apps, token)
            >>> if not valid:
            ...     for error in errors:
            ...         print(error)
            ...     raise Exception("Repository validation failed")
            >>> # After validation, each app.repo_url contains the working URL
        """
        errors = []

        def validate_single(app):
            urls_to_try = []
            if app.repo_url:
                urls_to_try.append(("Custom URL", app.repo_url))
            else:
                urls_to_try.append(("HTTPS", f"https://github.com/{app.repo}.git"))
                if github_token:
                    urls_to_try.append(("Token", f"https://{github_token}@github.com/{app.repo}.git"))
                urls_to_try.append(("SSH", f"git@github.com:{app.repo}.git"))

            last_error = None
            for method_name, url in urls_to_try:
                try:
                    if app.is_commit:
                        cmd = ["git", "ls-remote", url, "HEAD"]
                    else:
                        cmd = ["git", "ls-remote", "--heads", "--tags", url]
                        if app.ref:
                            cmd.extend([app.ref])

                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

                    if result.returncode == 0:
                        if app.is_commit:
                            if result.stdout:
                                app.repo_url = url
                                return (app, None)
                            last_error = "Repository exists but appears empty or inaccessible"
                        elif app.ref and not result.stdout:
                            last_error = f"Branch/tag '{app.ref}' not found"
                        else:
                            app.repo_url = url
                            return (app, None)
                    else:
                        last_error = result.stderr.strip()
                except subprocess.TimeoutExpired:
                    last_error = "Timeout while checking repository (network issue?)"
                except FileNotFoundError:
                    return (app, "❌ Git is not installed or not in PATH")
                except Exception as e:
                    last_error = str(e)

            if last_error and "not found" in last_error.lower():
                error_msg = f"❌ {app.repo}: Repository not found on GitHub."
            elif last_error and app.ref and "branch/tag" in last_error.lower():
                error_msg = (
                    f"❌ {app.repo}: Branch/tag '{app.ref}' not found. Check the branch name or use a valid tag/commit."
                )
            elif github_token:
                error_msg = f"❌ {app.repo}: Could not access repository with any authentication method. Last error: {last_error}"
            else:
                error_msg = f"❌ {app.repo}: Could not access repository. Repo may be private (use --github-token or configure SSH keys). Last error: {last_error}"
            return (app, error_msg)

        with ThreadPoolExecutor(max_workers=min(10, len(apps))) as executor:
            futures = [executor.submit(validate_single, app) for app in apps]
            for future in as_completed(futures):
                app, error = future.result()
                if error:
                    errors.append(error)
                    if "Git is not installed" in error:
                        return (False, errors)

        return (len(errors) == 0, errors)
