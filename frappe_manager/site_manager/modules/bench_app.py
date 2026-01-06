"""
BenchAppManager - Frappe App Management Module

This module handles all Frappe app-related operations within a bench including
app installation, removal, building, and branch management.

Extracted from the monolithic Bench class and BenchOperations for better 
separation of concerns.
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from frappe_manager import STABLE_APP_BRANCH_MAPPING_LIST
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker import DockerClient, DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.logger import log
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.site_exceptions import (
    BenchOperationBenchAppInSiteFailed,
    BenchOperationBenchBuildFailed,
    BenchOperationBenchInstallAppInPythonEnvFailed,
    BenchOperationBenchRemoveAppFromPythonEnvFailed,
    BenchOperationException,
    BenchOperationFrappeBranchChangeFailed,
)
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
        quiet: Whether to suppress output
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
        bench_name: str,
        bench_path: Path,
        docker_client: DockerClient,
        bench_config: BenchConfig,
        quiet: bool = False,
    ):
        """
        Initialize BenchAppManager.
        
        Args:
            bench_name: Name of the bench
            bench_path: Path to the bench directory on host
            docker_client: Docker client for container operations
            bench_config: Bench configuration object
            quiet: Whether to suppress output (default: False)
        """
        self.bench_name = bench_name
        self.bench_path = bench_path
        self.docker_client = docker_client
        self.bench_config = bench_config
        self.quiet = quiet
        self.logger = log.get_logger()
        
        # Derived paths and commands
        self.frappe_bench_dir: Path = bench_path / "workspace" / "frappe-bench"
        self.bench_cli_cmd = ['/opt/user/.bin/bench_orig']
    
    def install_apps(
        self, 
        apps_list: List[Dict[str, str]], 
        already_installed_apps: Dict[str, str] = None
    ) -> None:
        """
        Install multiple apps to the bench environment.
        
        This method handles installation of apps, removing prebaked apps that
        are not in the desired list, and installing new apps with their
        specified branches.
        
        Args:
            apps_list: List of dicts with 'app' and 'branch' keys
            already_installed_apps: Dict of already installed apps with branches
        
        Example:
            >>> app_manager.install_apps([
            ...     {"app": "frappe", "branch": "version-15"},
            ...     {"app": "erpnext", "branch": "version-15"},
            ... ])
        """
        if already_installed_apps is None:
            already_installed_apps = STABLE_APP_BRANCH_MAPPING_LIST
        
        to_install_apps = [x["app"] for x in apps_list]
        
        # Remove prebaked apps not in the install list
        for app, branch in already_installed_apps.items():
            if app == 'frappe':
                continue
            
            if app not in to_install_apps:
                richprint.change_head(f"Removing prebaked app {app} from python env.")
                self.remove_app_from_env(app)
                richprint.print(f"Removed prebaked app {app}")
        
        # Install apps from the list
        for app_info in apps_list:
            app = app_info["app"]
            branch = app_info.get("branch")
            
            status_txt = f"Building and Installing app {app} in env."
            if branch:
                status_txt = f"Building and Installing app {app} -> {branch}."
            
            richprint.change_head(status_txt)
            
            # Skip if already installed with same branch
            if app in already_installed_apps.keys():
                if already_installed_apps[app] == branch:
                    richprint.print(f"Skipped installation of prebaked app [blue]{app} -> {branch}[/blue].")
                    continue
                
                if not branch:
                    branch = already_installed_apps[app]
            
            self.install_app_to_env(app, branch)
            
            richprint.print(f"Builded and Installed app [blue]{app}{' -> ' + branch if branch else ''}[/blue] in env.")
    
    def install_app_to_env(
        self,
        app: str,
        branch: Optional[str] = None,
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
        parameters: Dict = {
            'branch': branch,
            'overwrite': overwrite,
            'skip_assets': skip_assets,
        }
        
        app_install_env_command = self.bench_cli_cmd + ["get-app"]
        app_install_env_command += parameters_to_options(parameters, exclude=["app"])
        app_install_env_command += [app]
        
        app_install_env_command = " ".join(app_install_env_command)
        app_install_exception = BenchOperationBenchInstallAppInPythonEnvFailed(
            bench_name=self.bench_name, 
            app_name=app
        )
        
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
            'no_backup': no_backup,
            'force': force,
        }
        
        app_rm_env_command = self.bench_cli_cmd + ["remove-app"]
        app_rm_env_command += parameters_to_options(parameters, exclude=["app"])
        app_rm_env_command += [app]
        
        app_rm_env_command = " ".join(app_rm_env_command)
        
        self._container_run(
            app_rm_env_command,
            raise_exception_obj=BenchOperationBenchRemoveAppFromPythonEnvFailed(
                bench_name=self.bench_name, 
                app_name=app
            ),
        )
    
    def install_app_to_site(
        self,
        app: str,
        site_name: Optional[str] = None,
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
            raise_exception_obj=BenchOperationBenchAppInSiteFailed(
                bench_name=self.bench_name, 
                app_name=app
            ),
        )
    
    def install_apps_to_site(
        self,
        site_name: Optional[str] = None,
    ) -> None:
        """
        Install all available apps to a site.
        
        Installs all apps found in the apps directory to the specified site.
        
        Args:
            site_name: Site name. Defaults to bench_name.
        
        Example:
            >>> app_manager.install_apps_to_site("example.localhost")
        """
        if site_name is None:
            site_name = self.bench_name
        
        for app in self.get_installed_apps_list():
            richprint.change_head(f"Installing app {app.name} in site.")
            self.install_app_to_site(app.name, site_name)
            richprint.print(f"Installed app {app.name} in site.")
    
    def build(self, app_list: Optional[List[str]] = None) -> None:
        """
        Build bench assets.
        
        This runs 'bench build' to compile and bundle frontend assets.
        
        Args:
            app_list: List of specific apps to build. If None, builds all apps.
        
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
        
        build_exception = BenchOperationBenchBuildFailed(
            bench_name=self.bench_name, 
            apps=app_list
        )
        
        build_cmd = " ".join(build_cmd)
        self._container_run(build_cmd, build_exception)
    
    def change_app_branch(
        self,
        app: str,
        branch: str,
        prebaked_branch: Optional[str] = None,
    ) -> None:
        """
        Change the branch of an installed app.
        
        This method changes the git branch of a prebaked app in the bench.
        
        Args:
            app: App name
            branch: Target branch
            prebaked_branch: Current prebaked branch (for comparison)
        
        Raises:
            BenchOperationFrappeBranchChangeFailed: If branch change fails
        
        Example:
            >>> app_manager.change_app_branch(
            ...     "frappe",
            ...     "version-15",
            ...     prebaked_branch="version-14"
            ... )
        """
        if prebaked_branch and branch == prebaked_branch:
            richprint.print(f"App {app} is already on branch {branch}")
            return
        
        richprint.change_head(f"Changing {app} app's branch to {branch}")
        
        if prebaked_branch:
            richprint.change_head(
                f"Changing prebaked {app} app's branch {prebaked_branch} -> {branch}"
            )
        
        change_branch_command = self.bench_cli_cmd + [f"get-app --overwrite --branch {branch} {app}"]
        change_branch_command = " ".join(change_branch_command)
        
        exception = BenchOperationFrappeBranchChangeFailed(
            bench_name=self.bench_name, 
            app=app, 
            branch=branch
        )
        
        self._container_run(command=change_branch_command, raise_exception_obj=exception)
        
        richprint.print(f"Changed {app} app's branch to {branch}")
    
    def get_installed_apps_list(self) -> List[Path]:
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
        apps_dir = self.frappe_bench_dir / 'apps'
        apps_dirs: List[Path] = [item for item in apps_dir.iterdir() if item.is_dir()]
        return apps_dirs
    
    def _container_run(
        self,
        command: str,
        raise_exception_obj: Optional[BenchOperationException] = None,
        capture_output: bool = False,
        user: str = "frappe",
        workdir: str = "/workspace/frappe-bench",
        service: str = 'frappe',
    ):
        """
        Execute a command inside the bench container.
        
        This is an internal helper method that wraps docker_client.compose.exec
        with appropriate error handling and output streaming.
        
        Args:
            command: Shell command to execute
            raise_exception_obj: Exception to raise on failure
            capture_output: Whether to capture output instead of streaming
            user: User to run command as (default: frappe)
            workdir: Working directory (default: /workspace/frappe-bench)
            service: Docker service name (default: frappe)
        
        Returns:
            SubprocessOutput if capture_output=True, None otherwise
        
        Raises:
            BenchOperationException: If command fails and raise_exception_obj is provided
            DockerException: If command fails and no exception object provided
        """
        # Wrap command in bash with proper environment
        command = f"/bin/bash -c 'source /etc/bash.bashrc; {command}'"
        
        try:
            if capture_output:
                output: SubprocessOutput = self.docker_client.compose.exec(
                    service=service,
                    command=command,
                    user=user,
                    workdir=workdir,
                    stream=not capture_output
                )
                return output
            else:
                output: Iterable[Tuple[str, bytes]] = self.docker_client.compose.exec(
                    service=service,
                    command=command,
                    workdir=workdir,
                    user=user,
                    stream=not capture_output
                )
                richprint.live_lines(output)
        
        except DockerException as e:
            if raise_exception_obj:
                raise_exception_obj.set_output(e.output)
                raise raise_exception_obj
            raise e
