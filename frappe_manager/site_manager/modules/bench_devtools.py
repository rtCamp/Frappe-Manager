"""BenchDevTools - Development Tools Module

This module handles development-related operations for a bench including:
- VS Code container attachment
- Dev package installation/removal
- Debugger configuration
- VS Code configuration syncing
"""

import copy
import json
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager import (
    NVIM_DAP_LUA,
    VSCODE_LAUNCH_JSON,
    VSCODE_SETTINGS_JSON,
    VSCODE_TASKS_JSON,
)
from frappe_manager.site_manager.exceptions import (
    BenchAttachTocontainerFailed,
    BenchFailedToRemoveDevPackages,
    BenchNotRunning,
)
from frappe_manager.utils.helpers import capture_and_format_exception

if TYPE_CHECKING:
    from frappe_manager.docker.compose_file import ComposeFile
    from frappe_manager.docker.docker_client import DockerClient


class BenchDevTools:
    """Manages development tools and VS Code integration for a bench."""

    def __init__(
        self,
        docker_client: "DockerClient",
        compose_file_manager: "ComposeFile",
        bench_path: Path,
        bench_name: str,
        is_running_fn,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchDevTools module.

        Args:
            docker_client: Docker client instance
            compose_file_manager: Compose file manager
            bench_path: Path to bench directory
            bench_name: Name of the bench
            is_running_fn: Function to check if bench is running
            output_handler: Handler for output operations
        """
        self.docker_client = docker_client
        self.compose_file_manager = compose_file_manager
        self.bench_path = bench_path
        self.bench_name = bench_name
        self._is_running = is_running_fn
        self.output = output_handler or RichOutputHandler()
        self.logger: Any | None = None  # Set externally if needed

    def get_apps_dev_requirements(self) -> list[str]:
        """
        Parse dev requirements from all apps' pyproject.toml files.

        Returns:
            List of package specs with versions
        """
        apps_path = self.bench_path / "workspace" / "frappe-bench" / "apps"
        apps_path = apps_path.absolute()

        pattern = "**/pyproject.toml"
        pyproject_files = list(apps_path.glob(pattern))

        import tomlkit

        packages_list = []
        for pyproject_path in pyproject_files:
            pyproject = tomlkit.parse(pyproject_path.read_text())
            packages = pyproject.get("tool", {}).get("bench", {}).get("dev-dependencies", {})
            for name, version in packages.items():
                full_name = name + version
                packages_list.append(full_name)

        return packages_list

    def remove_dev_packages(self):
        """Remove dev packages from the bench environment."""
        self.output.change_head("Removing dev packages from env")
        dev_packages = self.get_apps_dev_requirements()
        remove_command = "/workspace/frappe-bench/env/bin/python -m pip uninstall --yes " + " ".join(dev_packages)
        try:
            self.docker_client.compose.exec("frappe", command=remove_command, user="frappe", stream=False)
        except DockerException as e:
            raise BenchFailedToRemoveDevPackages(self.bench_name)
        self.output.print("Removed dev packages from env")

    def install_dev_packages(self):
        """Install dev packages in the bench environment."""
        self.output.change_head("Installing dev packages in env")
        dev_packages = self.get_apps_dev_requirements()
        install_command = "/workspace/frappe-bench/env/bin/python -m pip install --quiet --upgrade " + " ".join(
            dev_packages,
        )
        try:
            self.docker_client.compose.exec("frappe", command=install_command, user="frappe", stream=False)
        except DockerException as e:
            raise BenchFailedToRemoveDevPackages(self.bench_name)
        self.output.print("Installed dev packages in env")

    def attach_to_bench(self, user: str, extensions: list[str], workdir: str, debugger: bool = False) -> None:
        """
        Attach to running bench container using VS Code Remote Containers.

        Args:
            user: Username to use in the container
            extensions: List of VS Code extensions to install
            workdir: Working directory path inside container
            debugger: Whether to setup debugging configuration

        Raises:
            BenchNotRunning: If bench container is not running
            BenchAttachTocontainerFailed: If attaching fails
        """
        self._verify_bench_running()

        if debugger:
            self._setup_debugger_config(workdir)

        self._verify_vscode_installed()

        container_name = self._get_frappe_container_name()
        vscode_cmd = self._build_vscode_command(container_name, workdir)

        self._update_container_config(user, sorted(extensions))
        self._attach_to_container(vscode_cmd)

    def _verify_bench_running(self) -> None:
        """Verify bench container is running."""
        if not self._is_running():
            raise BenchNotRunning(self.bench_name)

    def _verify_vscode_installed(self) -> None:
        """Verify VS Code is installed and accessible."""
        vscode_path = shutil.which("code")
        if not vscode_path:
            self.output.stop()
            self.output.display_error("Visual Studio Code binary i.e 'code' is not accessible via cli")

    def _get_frappe_container_name(self) -> str:
        """Get the frappe container name and encode it."""
        container_name = self.compose_file_manager.get_container_names()
        return container_name["frappe"].encode().hex()

    def _build_vscode_command(self, container_hex: str, workdir: str) -> str:
        """Build the VS Code remote container command."""
        vscode_path = shutil.which("code")
        assert vscode_path is not None, "VS Code binary not found"
        return shlex.join([vscode_path, f"--folder-uri=vscode-remote://attached-container+{container_hex}+{workdir}"])

    def _update_container_config(self, user: str, extensions: list[str]) -> None:
        """Update container configuration with user and extensions."""
        base_config = [
            {
                "remoteUser": user,
                "remoteEnv": {"SHELL": "/bin/bash"},
                "customizations": {
                    "vscode": {
                        "settings": VSCODE_SETTINGS_JSON,
                    },
                },
            },
        ]

        config_with_extensions = copy.deepcopy(base_config)
        config_with_extensions[0]["customizations"]["vscode"]["extensions"] = extensions

        labels = {"devcontainer.metadata": json.dumps(config_with_extensions)}

        previous_config = self._get_previous_container_config()

        if self._config_needs_update(previous_config, extensions, user):
            self._apply_new_config(labels)

    def _get_previous_container_config(self) -> list[str]:
        """Get previous container extension configuration."""
        try:
            labels = self.compose_file_manager.get_labels("frappe")[0]
            config = json.loads(labels["devcontainer.metadata"])
            return config["customizations"]["vscode"]["extensions"]
        except KeyError:
            return []

    def _config_needs_update(self, previous_extensions: list[str], new_extensions: list[str], user: str) -> bool:
        """Check if container config needs updating."""
        return previous_extensions != new_extensions

    def _apply_new_config(self, labels: dict) -> None:
        """Apply new container configuration."""
        self.output.change_head("Configuration changed, regenerating label in bench compose")
        self.compose_file_manager.configure_service("frappe", labels=labels)
        self.output.print("Regenerated bench compose")
        self.docker_client.compose.up(
            services=["frappe"],
            detach=True,
            pull="never",
            force_recreate=False,
        )

    def _setup_debugger_config(self, workdir: str) -> None:
        """Setup debugger configuration if workdir is in workspace."""
        workdir = workdir.strip("/")
        if not workdir.startswith("workspace"):
            self.output.warning("Debugger configuration is only supported for workspace directory")
            return

        self._sync_vscode_config_files(workdir)
        self._install_bench_dev_tools()
        self.output.print("Synced vscode debugger configuration")

    def _sync_vscode_config_files(self, workdir: str) -> None:
        """Sync VS Code configuration files."""
        workdir = workdir.strip("/")
        vscode_dir = self.bench_path / workdir / ".vscode"
        vscode_dir.mkdir(exist_ok=True, parents=True)

        config_files = {"tasks": VSCODE_TASKS_JSON, "launch": VSCODE_LAUNCH_JSON, "settings": VSCODE_SETTINGS_JSON}

        for filename, content in config_files.items():
            file_path = vscode_dir / f"{filename}.json"
            if file_path.exists():
                self._backup_config_file(file_path)
            self._write_config_file(file_path, content)

    def _backup_config_file(self, file_path: Path) -> None:
        """Backup existing config file."""
        timestamp = datetime.now().strftime("%d-%b-%y--%H-%M-%S")
        extension = file_path.suffix or ".bak"
        backup_path = file_path.parent / f"{file_path.stem}.{timestamp}{extension}"
        shutil.copy2(file_path, backup_path)
        self.output.print(f"Backup previous '{file_path.name}' : {backup_path}")

    def _write_config_file(self, file_path: Path, content: dict) -> None:
        """Write new config file."""
        with open(file_path, "w+") as f:
            f.write(json.dumps(content, indent=4, sort_keys=True))

    def _install_bench_dev_tools(self) -> None:
        """Install development Python tools in the bench environment."""
        try:
            self.docker_client.compose.exec(
                service="frappe",
                command="/workspace/frappe-bench/env/bin/pip install ruff debugpy",
                user="frappe",
                stream=True,
            )
        except DockerException as e:
            if self.logger:
                self.logger.error(f"bench dev tools installation exception: {capture_and_format_exception()}")
            self.output.warning("Not able to install bench dev tools (ruff/debugpy) in env")

    def _attach_to_container(self, vscode_cmd: str) -> None:
        """Attach to the container using VS Code."""
        self.output.change_head("Attaching to Container")
        output = subprocess.run(vscode_cmd, shell=True)

        if output.returncode != 0:
            raise BenchAttachTocontainerFailed(self.bench_name, "frappe")

    # ------------------------------------------------------------------
    # Neovim / nvim-dap integration
    # ------------------------------------------------------------------

    def setup_neovim_debugger(self, workdir: str) -> None:
        """
        Write nvim-dap configuration for Frappe debugging.

        Creates a ``.nvim.lua`` file in the bench workspace directory that
        configures `nvim-dap` with the same debug sessions available via
        the VS Code ``--debugger`` flag:

        - **fm-frappe-debug** – dev-server with debugpy attached (kills port first)
        - **Debug Specific Queue** – attach to a background worker queue
        - **Debug Specific Function** – run a single ``frappe.execute()`` call

        The file is written to the *host* path that maps to ``workdir`` inside
        the container (e.g. ``<bench_path>/workspace/frappe-bench/.nvim.lua``).
        Neovim loads ``.nvim.lua`` automatically when
        ``vim.opt.exrc = true`` is set (Neovim ≥ 0.9 trusted files) and the
        directory is opened as a workspace.

        Also installs ``ruff`` and ``debugpy`` in the bench env so formatting
        and debug adapter support are available.

        Args:
            workdir: Working directory path inside the container
                     (e.g. ``/workspace/frappe-bench``).  Must start with
                     ``workspace`` after stripping leading slashes.
        """
        workdir = workdir.strip("/")
        if not workdir.startswith("workspace"):
            self.output.warning("Neovim debugger configuration is only supported for workspace directory")
            return

        self._sync_nvim_config_files(workdir)
        self._install_bench_dev_tools()
        self.output.print("Synced nvim-dap debugger configuration (.nvim.lua)")

    def _sync_nvim_config_files(self, workdir: str) -> None:
        """
        Write the ``.nvim.lua`` project-local DAP config into the bench workspace.

        The file is placed at ``<bench_path>/<workdir>/.nvim.lua`` so it sits
        at the root of the Frappe bench tree that a developer opens in Neovim.
        Any pre-existing file is backed up with a timestamped copy first.

        Args:
            workdir: Relative workspace path (leading/trailing slashes already stripped).
        """
        workdir = workdir.strip("/")
        workspace_dir = self.bench_path / workdir
        workspace_dir.mkdir(exist_ok=True, parents=True)

        nvim_lua_path = workspace_dir / ".nvim.lua"
        if nvim_lua_path.exists():
            self._backup_config_file(nvim_lua_path)

        with open(nvim_lua_path, "w") as f:
            f.write(NVIM_DAP_LUA)
