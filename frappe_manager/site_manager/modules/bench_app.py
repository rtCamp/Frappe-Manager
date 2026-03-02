"""
BenchAppManager - Frappe App Management Module

This module handles all Frappe app-related operations within a bench including
app installation, removal, building, and branch management.

Extracted from the monolithic Bench class and BenchOperations for better
separation of concerns.
"""

import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from frappe_manager.docker import DockerClient, DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import AppConfig, BenchConfig
from frappe_manager.site_manager.exceptions import (
    BenchOperationBenchAppInSiteFailed,
    BenchOperationBenchBuildFailed,
    BenchOperationBenchInstallAppInPythonEnvFailed,
    BenchOperationBenchRemoveAppFromPythonEnvFailed,
    BenchOperationException,
)
from frappe_manager.site_manager.modules.app_cloner import AppCloner, AppClonerError
from frappe_manager.utils.docker import parameters_to_options


class BenchAppManager:
    """
    Manages Frappe app operations within a bench.

    This module is responsible for all app-related operations including:
    - App installation to Python environment
    - App installation to site
    - App removal from Python environment
    - App building (bench build)
    - App branch management
    - App listing

    The module encapsulates bench command execution and provides a clean
    interface for app management operations.

    Attributes:
        bench_name: Name of the bench
        bench_path: Path to the bench directory
        docker_client: Docker client for container operations
        bench_config: Bench configuration object
        logger: Logger instance
        frappe_bench_dir: Path to frappe-bench directory inside container
        bench_cli_cmd: Base bench command prefix

    Example:
        >>> app_manager = BenchAppManager(
        ...     bench_name="example.localhost",
        ...     bench_path=Path("/home/user/frappe/example.localhost"),
        ...     docker_client=docker_client,
        ...     bench_config=bench_config,
        ... )
        >>> app_manager.install_app_to_env("erpnext", branch="version-15")
        >>> app_manager.install_app_to_site("erpnext", "example.localhost")
    """

    def __init__(
        self,
        logger: ContextualLogger,
        bench_name: str,
        bench_path: Path,
        docker_client: DockerClient,
        bench_config: BenchConfig,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchAppManager.

        Args:
            logger: Contextual logger for audit/debug logging
            bench_name: Name of the bench
            bench_path: Path to the bench directory on host
            docker_client: Docker client for container operations
            bench_config: Bench configuration object
            output_handler: Handler for output operations
        """
        self.logger = logger.child(component="app_manager")
        self.bench_name = bench_name
        self.bench_path = bench_path
        self.docker_client = docker_client
        self.bench_config = bench_config
        self.output = output_handler or RichOutputHandler()

        self.frappe_bench_dir: Path = bench_path / "workspace" / "frappe-bench"
        self.bench_cli_cmd = ["/usr/local/bin/bench"]

    def get_current_runtime_versions(self, use_run: bool = False) -> dict[str, str | None]:
        """Get currently installed Python and Node versions from the container."""
        import re

        versions: dict[str, str | None] = {}
        versions["python"] = None
        versions["node"] = None

        try:
            result = self._container_run(
                "/workspace/frappe-bench/env/bin/python --version",
                capture_output=True,
                raise_exception_obj=None,
                use_run=use_run,
            )
            if result and result.exit_code == 0:
                output = " ".join(result.combined)
                match = re.search(r"Python (\d+\.\d+\.\d+)", output)
                if match:
                    versions["python"] = match.group(1)
        except Exception:
            pass

        try:
            result = self._container_run(
                "node --version",
                capture_output=True,
                raise_exception_obj=None,
                use_run=use_run,
            )
            if result and result.exit_code == 0:
                output = " ".join(result.combined)
                match = re.search(r"v(\d+\.\d+\.\d+)", output)
                if match:
                    versions["node"] = match.group(1)
        except Exception:
            pass

        return versions

    def setup_python_and_node_environments(self, use_run: bool = False) -> bool:
        """
        Setup Python and Node.js environments for the bench.

        This method:
        1. Installs the required Python version via UV if not already present
        2. Creates venv with UV using that Python version
        3. Installs Node version via fnm if not present
        4. Sets fnm default to use that Node version

        Returns:
            dict: {
                'venv_recreated': bool,
                'old_python_version': str or None,
                'old_node_version': str or None
            }
        """
        from frappe_manager.site_manager.bench_config import (
            parse_node_version_for_runtime,
            parse_python_version_for_runtime,
        )

        venv_recreated = False
        python_version_requirement = self.bench_config.python_version
        node_version_requirement = self.bench_config.node_version

        if python_version_requirement:
            self.output.change_head("Checking Python version compatibility")

            from frappe_manager.site_manager.bench_config import extract_python_version_requirement

            frappe_app_path = self.bench_config.root_path / "workspace" / "frappe-bench" / "apps" / "frappe"
            frappe_python_req = None
            if frappe_app_path.exists():
                frappe_python_req = extract_python_version_requirement(frappe_app_path)

            try:
                check_current_version_cmd = "/workspace/frappe-bench/env/bin/python --version"
                result = self._container_run(
                    check_current_version_cmd,
                    capture_output=True,
                    raise_exception_obj=None,
                    use_run=use_run,
                )

                if result and result.exit_code == 0:
                    import re

                    current_version_output = " ".join(result.combined)
                    version_match = re.search(r"Python (\d+)\.(\d+)\.(\d+)", current_version_output)

                    if version_match:
                        current_major = int(version_match.group(1))
                        current_minor = int(version_match.group(2))

                        if self._python_version_satisfies_requirement(
                            current_major,
                            current_minor,
                            python_version_requirement,
                        ):
                            if frappe_python_req:
                                self.output.print(
                                    f"✓ Python {current_major}.{current_minor} already satisfies {python_version_requirement} "
                                    f"(Frappe requires: {frappe_python_req}) - skipping installation",
                                )
                            else:
                                self.output.print(
                                    f"✓ Python {current_major}.{current_minor} already satisfies {python_version_requirement} - skipping installation",
                                )
                            python_version_requirement = None
                        else:
                            self.output.print(
                                f"Python {current_major}.{current_minor} does not satisfy {python_version_requirement} - will recreate venv",
                            )

            except Exception as e:
                self.logger.debug(f"Could not check current Python version: {e}")

        if python_version_requirement:
            python_version = parse_python_version_for_runtime(python_version_requirement)
            if python_version:
                self.output.change_head(f"Setting up Python environment for requirement: {python_version_requirement}")
                try:
                    scan_pythons_cmd = """
if [ -d /workspace/.uv/python ]; then
    for dir in /workspace/.uv/python/cpython-*; do
        if [ -d "$dir" ]; then
            basename "$dir"
        fi
    done 2>/dev/null | sort -u
fi
"""
                    result = self._container_run(
                        scan_pythons_cmd,
                        capture_output=True,
                        raise_exception_obj=None,
                        use_run=use_run,
                    )

                    selected_python_full = None
                    selected_version = None
                    if result and result.exit_code == 0:
                        import re

                        candidates = []

                        for line in result.combined:
                            if "/usr/bin" in line or "download available" in line:
                                continue

                            match = re.search(r"cpython-(\d+)\.(\d+)\.(\d+)", line)
                            if match:
                                major = int(match.group(1))
                                minor = int(match.group(2))
                                patch = int(match.group(3))
                                if self._python_version_satisfies_requirement(major, minor, python_version_requirement):
                                    # Store the FULL directory name with platform suffix, not just cpython-X.Y.Z
                                    candidates.append((major, minor, patch, line.strip()))

                        if candidates:
                            candidates.sort(reverse=True)
                            selected_version = candidates[0]
                            selected_python_full = selected_version[3]
                            self.output.print(
                                f"Found Python {selected_version[0]}.{selected_version[1]}.{selected_version[2]} satisfying {python_version_requirement}",
                            )

                    if not selected_python_full:
                        self.output.print(f"Installing Python {python_version} via uv..")
                        quoted_pkg = shlex.quote(f"cpython-{python_version}")
                        install_cmd = f"uv python install {quoted_pkg}"
                        self._container_run(install_cmd, raise_exception_obj=None, use_run=use_run)

                        detect_installed_cmd = (
                            f"ls -1 /workspace/.uv/python/ | grep '^{quoted_pkg}' | sort -V | tail -1"
                        )
                        result = self._container_run(
                            detect_installed_cmd,
                            capture_output=True,
                            raise_exception_obj=None,
                            use_run=use_run,
                        )
                        if result and result.exit_code == 0 and result.combined:
                            selected_python_full = result.stdout[0].strip()
                        else:
                            selected_python_full = f"cpython-{python_version}"

                        self.output.print(f"Installed Python {python_version} via uv")

                    if selected_python_full:
                        update_symlink_cmd = f"""
                        cd /workspace/.uv
                        rm -f python-default
                        ln -sf python/{selected_python_full} python-default
                        """
                        self._container_run(update_symlink_cmd, raise_exception_obj=None, use_run=use_run)

                    self.output.change_head(f"Creating virtual environment with {selected_python_full}")
                    quoted_python = shlex.quote(selected_python_full)
                    recreate_venv_cmd = f"""
                    cd /workspace/frappe-bench
                    if [ -d env ]; then
                        mv env env.bak || rm -rf env.bak
                    fi
                    uv venv env --python {quoted_python} --seed --link-mode=copy
                    """
                    self._container_run(recreate_venv_cmd, raise_exception_obj=None, use_run=use_run)
                    selected_version_str = (
                        f"{selected_version[0]}.{selected_version[1]}.{selected_version[2]}"
                        if selected_version
                        else selected_python_full
                    )
                    self.output.print(f"Created virtual environment with Python {selected_version_str}")
                    venv_recreated = True

                except Exception as e:
                    self.output.warning(f"Failed to setup Python {python_version}: {e}")
                    self.output.warning("Continuing with default Python version")

        node_version_requirement = self.bench_config.node_version
        if node_version_requirement:
            self.output.change_head("Checking Node version compatibility")

            from frappe_manager.site_manager.bench_config import extract_node_version_requirement

            frappe_app_path = self.bench_config.root_path / "workspace" / "frappe-bench" / "apps" / "frappe"
            frappe_node_req = None
            if frappe_app_path.exists():
                frappe_node_req = extract_node_version_requirement(frappe_app_path)

            try:
                check_current_node_cmd = "node --version"
                result = self._container_run(
                    check_current_node_cmd,
                    capture_output=True,
                    raise_exception_obj=None,
                    use_run=use_run,
                )

                if result and result.exit_code == 0:
                    import re

                    current_node_output = " ".join(result.combined)
                    version_match = re.search(r"v(\d+\.\d+\.\d+)", current_node_output)

                    if version_match:
                        full_version = version_match.group(1)
                        current_major = int(full_version.split(".")[0])

                        if self._node_version_satisfies_requirement(current_major, node_version_requirement):
                            if frappe_node_req:
                                self.output.print(
                                    f"Node {current_major} already satisfies {node_version_requirement} "
                                    f"(Frappe requires: {frappe_node_req}) - skipping installation",
                                )
                            else:
                                self.output.print(
                                    f"Node {current_major} already satisfies {node_version_requirement} - skipping installation",
                                )
                            node_version_requirement = None
                        else:
                            self.output.print(
                                f"Node {current_major} does not satisfy {node_version_requirement} - will install",
                            )

            except Exception as e:
                self.logger.debug(f"Could not check current Node version: {e}")

        if node_version_requirement:
            node_version = parse_node_version_for_runtime(node_version_requirement)
            if node_version:
                self.output.change_head(f"Setting up Node {node_version} environment")
                try:
                    check_cmd = f"fnm list | grep 'v{node_version}' || true"
                    result = self._container_run(
                        check_cmd,
                        capture_output=True,
                        raise_exception_obj=None,
                        use_run=use_run,
                    )

                    needs_install = True
                    if result and result.combined:
                        output_text = " ".join(result.combined)
                        if f"v{node_version}" in output_text:
                            needs_install = False
                            self.output.print(f"Node {node_version} already installed")

                    if needs_install:
                        self.output.print(f"Installing Node {node_version} via fnm..")
                        install_cmd = f"fnm install {node_version}"
                        install_result = self._container_run(
                            install_cmd,
                            capture_output=True,
                            raise_exception_obj=None,
                            use_run=use_run,
                        )
                        if install_result and install_result.exit_code == 0:
                            self.output.print(f"Installed Node {node_version} via fnm")
                        else:
                            raise Exception(
                                f"fnm install {node_version} failed with exit code {install_result.exit_code if install_result else 'unknown'}",
                            )

                    set_default_cmd = f"fnm default {node_version}"
                    default_result = self._container_run(
                        set_default_cmd,
                        capture_output=True,
                        raise_exception_obj=None,
                        use_run=use_run,
                    )
                    if default_result and default_result.exit_code == 0:
                        self.output.print(f"Set Node {node_version} as default")
                    else:
                        self.output.warning(f"Could not set Node {node_version} as default, but continuing")

                    yarn_check = f"test -f /workspace/.fnm/node-versions/v{node_version}/installation/bin/yarn || npm install -g yarn"
                    self._container_run(yarn_check, capture_output=True, raise_exception_obj=None, use_run=use_run)
                    self.output.print(f"Ensured yarn is installed for Node {node_version}")

                except Exception as e:
                    self.output.warning(f"Failed to setup Node {node_version}: {e}")
                    self.output.warning("Continuing with default Node version")

        return venv_recreated

    def _python_version_satisfies_requirement(self, current_major: int, current_minor: int, requirement: str) -> bool:
        import re

        requirement = requirement.strip()
        current_version = f"{current_major}.{current_minor}"

        if ">=" in requirement and "<" in requirement:
            match_min = re.search(r">=(\d+)\.(\d+)", requirement)
            match_max = re.search(r"<(\d+)\.(\d+)", requirement)

            if match_min and match_max:
                min_major, min_minor = int(match_min.group(1)), int(match_min.group(2))
                max_major, max_minor = int(match_max.group(1)), int(match_max.group(2))

                if current_major > min_major or (current_major == min_major and current_minor >= min_minor):
                    if current_major < max_major or (current_major == max_major and current_minor < max_minor):
                        return True

        elif requirement.startswith("^"):
            match = re.search(r"\^(\d+)\.(\d+)", requirement)
            if match:
                req_major, req_minor = int(match.group(1)), int(match.group(2))
                return current_major == req_major and current_minor >= req_minor

        elif re.match(r"^\d+\.\d+$", requirement):
            return current_version == requirement

        return False

    def _node_version_satisfies_requirement(self, current_major: int, requirement: str) -> bool:
        import re

        requirement = requirement.strip()

        if ">=" in requirement:
            match = re.search(r">=(\d+)", requirement)
            if match:
                min_major = int(match.group(1))
                return current_major >= min_major

        elif requirement.startswith("^"):
            match = re.search(r"\^(\d+)", requirement)
            if match:
                req_major = int(match.group(1))
                return current_major == req_major

        elif re.match(r"^\d+$", requirement):
            return current_major == int(requirement)

        return False

    def install_apps(
        self,
        apps_list: list[AppConfig],
        github_token: str | None = None,
        use_uv: bool = True,
        force_reinstall: bool = False,
        clone_only: bool = False,
        skip_clone: bool = False,
        use_run: bool = False,
    ) -> list[AppConfig]:
        """
        Install apps to the bench (clone, install Python/Node deps, build).

        Returns:
            List of AppConfig objects with corrected module names after cloning
        """
        if not apps_list:
            self.output.print("No apps to install")
            return []

        apps_config = apps_list

        if not skip_clone:
            self.output.change_head(f"Cloning {len(apps_config)} apps in parallel")
            try:
                cloner = AppCloner(
                    logger=self.logger,
                    apps_dir=self.frappe_bench_dir / "apps",
                    github_token=github_token,
                    output_handler=self.output,
                )
                cloned_apps = cloner.clone_apps_parallel(apps_config)
                self.output.print(f"Cloned {len(cloned_apps)} apps successfully")

                self._update_apps_txt(apps_config)
                self._update_apps_list_with_corrected_names(apps_config)
            except AppClonerError as e:
                raise BenchOperationBenchInstallAppInPythonEnvFailed(
                    bench_name=self.bench_name,
                    app_name="multiple apps",
                ) from e

        if clone_only:
            return apps_config

        self.output.change_head("Installing Python dependencies")
        self._install_python_deps_with_uv(apps_config, use_uv=use_uv, use_run=use_run)
        self.output.print("Installed Python dependencies")

        self.output.change_head("Installing Node dependencies")
        self._install_node_deps(use_run=use_run)
        self.output.print("Installed Node dependencies")

        self.output.change_head("Building frontend assets")
        self.build(use_run=use_run)
        self.output.print("Built frontend assets")

        return apps_config

    def _create_venv(self) -> None:
        venv_path = "/workspace/frappe-bench/env"
        create_venv_cmd = f"uv venv {venv_path} --python 3.12"

        try:
            self._container_run(create_venv_cmd, raise_exception_obj=None)
        except Exception as e:
            self.logger.warning(f"Failed to create venv with UV: {e}, trying python -m venv")
            create_venv_cmd = f"python3.12 -m venv {venv_path}"
            self._container_run(create_venv_cmd, raise_exception_obj=None)

    def _install_python_deps_with_uv(self, apps: list[AppConfig], use_uv: bool = True, use_run: bool = False) -> None:
        """
        Install Python dependencies using UV (much faster than pip).

        Strategy:
        1. Try UV first (10-100x faster)
        2. Fall back to pip if UV fails

        UV targets the bench virtual environment at /workspace/frappe-bench/env
        """
        if use_uv:
            try:
                check_cmd = "which uv"
                self._container_run(check_cmd, capture_output=True, raise_exception_obj=None, use_run=use_run)

                for app in apps:
                    install_cmd = [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        "/workspace/frappe-bench/env/bin/python",
                        "--no-cache-dir",
                        "-e",
                        f"apps/{app.name}",
                    ]
                    install_cmd_str = " ".join(install_cmd)
                    self._container_run(install_cmd_str, raise_exception_obj=None, use_run=use_run)

                return
            except Exception as uv_error:
                self.logger.debug(f"UV installation failed, attempting pip fallback: {uv_error}")

                try:
                    install_cmd = " ".join(self.bench_cli_cmd + ["setup", "requirements", "--python"])
                    self._container_run(install_cmd, use_run=use_run)
                    self.output.warning("UV installation failed, successfully fell back to pip")
                except Exception:
                    raise

                return

        install_cmd = " ".join(self.bench_cli_cmd + ["setup", "requirements", "--python"])
        self._container_run(install_cmd, use_run=use_run)

    def _install_node_deps(self, use_run: bool = False) -> None:
        """Install Node.js dependencies for all apps."""
        node_cmd = " ".join(self.bench_cli_cmd + ["setup", "requirements", "--node"])
        self._container_run(node_cmd, use_run=use_run)

    def _update_apps_txt(self, apps: list[AppConfig]) -> None:
        """
        Update apps.txt to register newly cloned apps.

        The apps.txt file lists all apps in the bench and is required
        for Frappe to recognize and install apps to sites.

        Args:
            apps: List of AppConfig objects for apps to add
        """
        apps_txt_path = self.frappe_bench_dir / "sites" / "apps.txt"

        # Read existing apps
        existing_apps = []
        if apps_txt_path.exists():
            existing_apps = apps_txt_path.read_text().strip().split("\n")
            existing_apps = [app.strip() for app in existing_apps if app.strip()]

        # Add new apps (avoid duplicates)
        for app_config in apps:
            if app_config.name not in existing_apps:
                existing_apps.append(app_config.name)

        # Write back to apps.txt
        apps_txt_path.write_text("\n".join(existing_apps) + "\n")
        self.logger.debug(f"Updated apps.txt with {len(apps)} new apps")

    def _update_apps_list_with_corrected_names(self, apps_config: list[AppConfig]) -> None:
        for i, app_config in enumerate(apps_config):
            if i < len(self.bench_config.apps_list):
                self.bench_config.apps_list[i] = app_config

    def install_app_to_env(
        self,
        app: str,
        branch: str | None = None,
        overwrite: bool = True,
        skip_assets: bool = False,
    ) -> None:
        """
        Install an app to the bench Python environment.

        This runs 'bench get-app' to clone and install the app in the
        bench's Python environment.

        Args:
            app: App name or URL
            branch: Git branch to install (optional)
            overwrite: Whether to overwrite if app exists
            skip_assets: Whether to skip building assets

        Raises:
            BenchOperationBenchInstallAppInPythonEnvFailed: If installation fails

        Example:
            >>> app_manager.install_app_to_env("erpnext", branch="version-15")
        """
        parameters: dict = {
            "branch": branch,
            "overwrite": overwrite,
            "skip_assets": skip_assets,
        }

        app_install_env_command = self.bench_cli_cmd + ["get-app"]
        app_install_env_command += parameters_to_options(parameters, exclude=["app"])
        app_install_env_command += [app]

        app_install_env_command = " ".join(app_install_env_command)
        app_install_exception = BenchOperationBenchInstallAppInPythonEnvFailed(bench_name=self.bench_name, app_name=app)

        self._container_run(
            app_install_env_command,
            raise_exception_obj=app_install_exception,
        )

    def remove_app_from_env(
        self,
        app: str,
        no_backup: bool = True,
        force: bool = True,
    ) -> None:
        """
        Remove an app from the bench Python environment.

        This runs 'bench remove-app' to remove the app from the environment.

        Args:
            app: App name to remove
            no_backup: Skip backup before removal
            force: Force removal without confirmation

        Raises:
            BenchOperationBenchRemoveAppFromPythonEnvFailed: If removal fails

        Example:
            >>> app_manager.remove_app_from_env("erpnext")
        """
        parameters: dict = {
            "no_backup": no_backup,
            "force": force,
        }

        app_rm_env_command = self.bench_cli_cmd + ["remove-app"]
        app_rm_env_command += parameters_to_options(parameters, exclude=["app"])
        app_rm_env_command += [app]

        app_rm_env_command = " ".join(app_rm_env_command)

        self._container_run(
            app_rm_env_command,
            raise_exception_obj=BenchOperationBenchRemoveAppFromPythonEnvFailed(
                bench_name=self.bench_name,
                app_name=app,
            ),
        )

    def install_app_to_site(
        self,
        app: str,
        site_name: str | None = None,
    ) -> None:
        """
        Install an app to a Frappe site.

        This runs 'bench --site <site> install-app' to install the app
        to the specified site.

        Args:
            app: App name to install
            site_name: Site name. Defaults to bench_name.

        Raises:
            BenchOperationBenchAppInSiteFailed: If installation fails

        Example:
            >>> app_manager.install_app_to_site("erpnext", "example.localhost")
        """
        if site_name is None:
            site_name = self.bench_name

        app_install_site_command = self.bench_cli_cmd + ["--site", site_name]
        app_install_site_command += ["install-app", app]
        app_install_site_command = " ".join(app_install_site_command)

        self._container_run(
            app_install_site_command,
            raise_exception_obj=BenchOperationBenchAppInSiteFailed(bench_name=self.bench_name, app_name=app),
        )

    def install_apps_to_site(
        self,
        site_name: str | None = None,
    ) -> None:
        """
        Install all available apps to a site in the order specified in bench_config.

        Installs apps in the same order as provided by the user to respect dependencies.

        Args:
            site_name: Site name. Defaults to bench_name.

        Example:
            >>> app_manager.install_apps_to_site("example.localhost")
        """
        if site_name is None:
            site_name = self.bench_name

        for app_config in self.bench_config.apps_list:
            app_name = app_config.name
            self.output.change_head(f"Installing app {app_name} in site")
            self.install_app_to_site(app_name, site_name)
            self.output.print(f"Installed app {app_name} in site")

    def build(self, app_list: list[str] | None = None, use_run: bool = False) -> None:
        """
        Build bench assets.

        This runs 'bench build' to compile and bundle frontend assets.

        Args:
            app_list: List of specific apps to build. If None, builds all apps.
            use_run: If True, use 'docker compose run --rm' instead of 'exec'

        Raises:
            BenchOperationBenchBuildFailed: If build fails

        Example:
            >>> app_manager.build()  # Build all apps
            >>> app_manager.build(["frappe", "erpnext"])  # Build specific apps
        """
        build_cmd = self.bench_cli_cmd + ["build"]

        if app_list is not None:
            for app in app_list:
                build_cmd += ["--app"] + [app]

        build_exception = BenchOperationBenchBuildFailed(bench_name=self.bench_name, apps=app_list)

        build_cmd = " ".join(build_cmd)
        self._container_run(build_cmd, build_exception, use_run=use_run)

    def get_installed_apps_list(self) -> list[Path]:
        """
        Get list of installed apps in the bench.

        Returns list of app directories found in the apps folder.

        Returns:
            List of Path objects for each app directory

        Example:
            >>> apps = app_manager.get_installed_apps_list()
            >>> print([app.name for app in apps])
            ['frappe', 'erpnext', 'hrms']
        """
        apps_dir = self.frappe_bench_dir / "apps"
        apps_dirs: list[Path] = [item for item in apps_dir.iterdir() if item.is_dir()]
        return apps_dirs

    def _filter_docker_warnings(self, output: SubprocessOutput) -> SubprocessOutput:
        """Filter out Docker Compose warning messages from captured output."""
        import re

        if not output.combined:
            return output

        filtered_lines = []
        docker_warning_pattern = re.compile(r'^time=".*?" level=(warning|info|error) msg=')

        for line in output.combined:
            if not docker_warning_pattern.match(line.strip()):
                filtered_lines.append(line)

        output.combined = filtered_lines
        return output

    def _container_run(
        self,
        command: str,
        raise_exception_obj: BenchOperationException | None = None,
        capture_output: bool = False,
        user: str = "frappe",
        workdir: str = "/workspace/frappe-bench",
        service: str = "frappe",
        use_run: bool = False,
    ) -> SubprocessOutput | None:
        """
        Execute a command inside the bench container.

        This is an internal helper method that wraps docker_client.compose.exec
        or docker_client.compose.run depending on use_run parameter.

        Args:
            command: Shell command to execute
            raise_exception_obj: Exception to raise on failure
            capture_output: Whether to capture output instead of streaming
            user: User to run command as (default: frappe)
            workdir: Working directory (default: /workspace/frappe-bench)
            service: Docker service name (default: frappe)
            use_run: If True, use 'docker compose run --rm' instead of 'exec' (default: False)

        Returns:
            SubprocessOutput if capture_output=True, None otherwise

        Raises:
            BenchOperationException: If command fails and raise_exception_obj is provided
            DockerException: If command fails and no exception object provided
        """
        try:
            if use_run:
                wrapped_command = f"cd {workdir} && {command}"
                run_command = f"exec-command.sh /bin/bash -c '{wrapped_command}'"
                if capture_output:
                    output = cast(
                        "SubprocessOutput",
                        self.docker_client.compose.run(
                            service=service,
                            command=run_command,
                            rm=True,
                            stream=False,
                            entrypoint="/exec-entrypoint.sh",
                        ),
                    )
                    output = self._filter_docker_warnings(output)
                    return output
                output = cast(
                    "Iterator[tuple[str, bytes]]",
                    self.docker_client.compose.run(
                        service=service,
                        command=run_command,
                        rm=True,
                            entrypoint="/exec-entrypoint.sh",
                        stream=True,
                    ),
                )
                self.output.live_lines(output)
            else:
                exec_command = f"/bin/bash -c '{command}'"
                if capture_output:
                    output = cast(
                        "SubprocessOutput",
                        self.docker_client.compose.exec(
                            service=service,
                            command=exec_command,
                            user=user,
                            workdir=workdir,
                            stream=False,
                        ),
                    )
                    output = self._filter_docker_warnings(output)
                    return output
                output = cast(
                    "Iterator[tuple[str, bytes]]",
                    self.docker_client.compose.exec(
                        service=service,
                        command=exec_command,
                        workdir=workdir,
                        user=user,
                        stream=True,
                    ),
                )
                self.output.live_lines(output)

        except DockerException as e:
            if raise_exception_obj:
                raise_exception_obj.set_output(e.output)
                raise raise_exception_obj
            raise e
