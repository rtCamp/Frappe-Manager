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
from typing import TYPE_CHECKING

from frappe_manager import BENCH_PYTHON, CONTAINER_BENCH_DIR
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.logger import get_logger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.exceptions import (
    BenchAttachTocontainerFailed,
    BenchFailedToInstallDevPackages,
    BenchFailedToRemoveDevPackages,
    BenchNotRunning,
)
from frappe_manager.utils.helpers import capture_and_format_exception
from frappe_manager.utils.site import host_bench_dir

if TYPE_CHECKING:
    from frappe_manager.docker.compose_file import ComposeFile
    from frappe_manager.docker.docker_client import DockerClient


def _pip_failure_message(summary: str, error: DockerException) -> str:
    """pip's own output is the only thing that says WHY the packages failed, so it
    travels with the message instead of dying with the DockerException."""
    reason = "\n".join(error.output.combined).strip()
    return f"{summary}\n{reason}" if reason else summary


class BenchDevTools:
    """Manages development tools and VS Code integration for a bench."""

    def __init__(
        self,
        docker_client: "DockerClient",
        compose_file_manager: "ComposeFile",
        bench_path: Path,
        bench_name: str,
        is_running_fn,
        is_image_runtime_fn=None,
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
        # Lazy: the bench config is loaded after this module is constructed. Image runtime binds
        # only the data paths (compose_shape.data_binds), so the host's workspace/.vscode is not
        # mounted anywhere and writing there is a no-op the container never sees.
        self._is_image_runtime = is_image_runtime_fn or (lambda: False)
        self.output = output_handler or RichOutputHandler()
        self.logger = get_logger(component="devtools")

    def get_apps_dev_requirements(self) -> list[str]:
        """
        Parse dev requirements from all apps' pyproject.toml files.

        Returns:
            List of package specs with versions
        """
        apps_path = host_bench_dir(self.bench_path) / "apps"
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
        remove_command = f"{BENCH_PYTHON} -m pip uninstall --yes " + " ".join(dev_packages)
        try:
            self.docker_client.compose.exec("frappe", command=remove_command, user="frappe", stream=False)
        except DockerException as e:
            # `from e` so docker's own reason survives: without it the user is told only that
            # dev packages failed, and the actual pip error is discarded.
            raise BenchFailedToRemoveDevPackages(
                self.bench_name,
                _pip_failure_message("Not able pip uninstall dev packages.", e),
            ) from e
        self.output.print("Removed dev packages from env")

    def install_dev_packages(self):
        """Install dev packages in the bench environment."""
        self.output.change_head("Installing dev packages in env")
        dev_packages = self.get_apps_dev_requirements()
        install_command = f"{BENCH_PYTHON} -m pip install --quiet --upgrade " + " ".join(
            dev_packages,
        )
        try:
            self.docker_client.compose.exec("frappe", command=install_command, user="frappe", stream=False)
        except DockerException as e:
            # Install-specific: reporting a failed install as a failed REMOVAL sends the user looking for the wrong problem.
            raise BenchFailedToInstallDevPackages(
                self.bench_name,
                _pip_failure_message("Not able pip install dev packages.", e),
            ) from e
        self.output.print("Installed dev packages in env")

    def attach_to_bench(
        self, user: str, extensions: list[str], workdir: str, debugger: bool = False, attach: bool = True
    ) -> None:
        """Prepare the bench for VS Code, then attach this machine's editor to it if it can.

        Preparation is the durable half and always runs: the `devcontainer.metadata` label carries
        the extensions, remoteUser and settings, and VS Code reads it however you connect --
        including Remote-SSH to this host followed by "Attach to Running Container". Launching a
        local editor is a convenience for whoever is sitting at a desktop.

        So a missing `code` CLI is not a failure: the bench IS prepared, and there is a documented
        way to finish from another machine. It used to raise, after writing the debug config, so a
        server run reported failure for work that had actually succeeded.
        """
        self._verify_bench_running()

        # The label update comes FIRST because it can RECREATE the frappe container, and a recreate
        # replaces the container filesystem. In image runtime the debug config is written inside the
        # container (nothing mounts it on the host), so writing it first meant fm created the files
        # and then destroyed them in the next step, silently. Proven on a live bench: a file written
        # into the container is GONE after a force-recreate. Mount runtime is unaffected either way,
        # since its .vscode lives on the host.
        self._update_container_config(user, sorted(extensions))

        if debugger:
            self._setup_debugger_config(workdir)

        vscode_path = shutil.which("code") if attach else None
        if vscode_path is None:
            self._report_manual_attach(reason_is_flag=not attach)
            return

        container_name = self._get_frappe_container_name()
        self._attach_to_container(self._build_vscode_command(vscode_path, container_name, workdir))

    def _report_manual_attach(self, reason_is_flag: bool) -> None:
        reason = (
            "--no-attach was given, so nothing was launched"
            if reason_is_flag
            else "the 'code' CLI is not on this machine, so nothing was launched"
        )
        self.output.print(f"Prepared '{self.bench_name}' for VS Code; {reason}.", emoji_code=":information:")
        self.output.print(
            "To connect from another machine: open VS Code there, Remote-SSH to this host, then run "
            f"'Dev Containers: Attach to Running Container' and pick {self._frappe_container_display_name()}.",
            emoji_code="",
        )

    def _frappe_container_display_name(self) -> str:
        return self.compose_file_manager.get_container_names()["frappe"]

    def _verify_bench_running(self) -> None:
        """Verify bench container is running."""
        if not self._is_running():
            raise BenchNotRunning(self.bench_name)

    def _get_frappe_container_name(self) -> str:
        """Get the frappe container name and encode it."""
        container_name = self.compose_file_manager.get_container_names()
        return container_name["frappe"].encode().hex()

    def _build_vscode_command(self, vscode_path: str, container_hex: str, workdir: str) -> str:
        """Build the VS Code remote container command. The caller resolved `code` already; looking
        it up a second time here needed an assert to convince a reader it could not be None."""
        return shlex.join([vscode_path, f"--folder-uri=vscode-remote://attached-container+{container_hex}+{workdir}"])

    def _update_container_config(self, user: str, extensions: list[str]) -> None:
        """Update container configuration with user and extensions."""
        from frappe_manager.site_manager import get_vscode_settings_json

        base_config = [
            {
                "remoteUser": user,
                "remoteEnv": {"SHELL": "/bin/bash"},
                # The bench answers on 80 INSIDE the container. VS Code forwards it to whoever
                # attached, so a bench on a remote host is reachable at localhost on the laptop
                # that attached to it -- the case fm exists for, and previously a manual ssh -L.
                "forwardPorts": [80],
                # Runs on every attach, so the linter the settings name is present without fm
                # exec'ing a pip install on its own schedule. Guarded, so it costs nothing when
                # ruff is already there.
                "postAttachCommand": (
                    f"test -x {CONTAINER_BENCH_DIR}/env/bin/ruff || {CONTAINER_BENCH_DIR}/env/bin/pip install ruff"
                ),
                "customizations": {
                    "vscode": {
                        "settings": get_vscode_settings_json(),
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

    def _get_previous_container_metadata(self) -> dict:
        """Parse back the devcontainer metadata this module wrote into the compose label.

        `ComposeFile.get_labels` returns the label MAPPING (or None when the service carries no
        labels), and `_apply_new_config` stores a JSON ARRAY under `devcontainer.metadata`.
        """
        labels = self.compose_file_manager.get_labels("frappe") or {}
        try:
            config = json.loads(labels["devcontainer.metadata"])
        except KeyError:
            return {}
        return config[0] if config else {}

    def _get_previous_container_config(self) -> list[str]:
        """Get previous container extension configuration."""
        vscode = self._get_previous_container_metadata().get("customizations", {}).get("vscode", {})
        return vscode.get("extensions", [])

    def _config_needs_update(self, previous_extensions: list[str], new_extensions: list[str], user: str) -> bool:
        """Check if container config needs updating."""
        if previous_extensions != new_extensions:
            return True
        # `user` becomes the remoteUser of the label; now that the extension comparison actually
        # works, an unread `user` would silently strip a changed --user from the container.
        return self._get_previous_container_metadata().get("remoteUser") != user

    def _apply_new_config(self, labels: dict) -> None:
        """Apply new container configuration."""
        self.output.change_head("Configuration changed, regenerating label in bench compose")
        self.compose_file_manager.configure_service("frappe", labels=labels)
        # A label is fixed at container creation, so the `up` below RECREATES frappe for the new
        # metadata to exist at all. Say so: this reported "Regenerated bench compose" and then
        # bounced the bench's web container without the word appearing anywhere.
        #
        # The FIRST `fm code` on any bench always lands here, because the label does not exist
        # until something writes it. Emitting it from `compose_shape.bench_service_specs` instead
        # would have the container born with it, leaving a recreate only for an operator who
        # actually asked for different --extension/--user values. Not done: it puts a dev-tooling
        # key in every bench's compose including prod, and a future change to DEFAULT_EXTENSIONS
        # would then recreate frappe on the next compose regeneration.
        self.output.print("Regenerated bench compose; recreating the frappe container to apply it")
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
        self._install_ruff()
        self.output.print("Synced vscode debugger configuration")

    def _vscode_config_files(self) -> dict:
        from frappe_manager.site_manager import get_vscode_launch_json, get_vscode_settings_json, get_vscode_tasks_json

        return {
            "tasks": get_vscode_tasks_json(),
            "launch": get_vscode_launch_json(),
            "settings": get_vscode_settings_json(),
        }

    def _sync_vscode_config_files(self, workdir: str) -> None:
        """Write the .vscode files where the RUNTIME can actually read them.

        Mount runtime binds the whole `./workspace`, so the host copy is the container's. Image
        runtime binds only the data paths, so a host write lands in a directory nothing mounts:
        the files appeared, fm claimed success, and the container -- which is what VS Code attaches
        to -- never saw them. There they go into the container instead, which is where they can be
        read, and like every other image-runtime edit they last until the next deploy.
        """
        if self._is_image_runtime():
            self._write_config_in_container(workdir, self._vscode_config_files())
            return

        workdir = workdir.strip("/")
        vscode_dir = self.bench_path / workdir / ".vscode"
        vscode_dir.mkdir(exist_ok=True, parents=True)

        for filename, content in self._vscode_config_files().items():
            file_path = vscode_dir / f"{filename}.json"
            rendered = json.dumps(content, indent=4, sort_keys=True)
            # Compare first: this used to back up and rewrite all three files on EVERY run, and the
            # backup name is timestamped to the second, so a directory collected three more stale
            # files per invocation and nothing ever removed them. A backup now means a real change.
            if file_path.exists():
                if file_path.read_text() == rendered:
                    continue
                self._backup_config_file(file_path)
            file_path.write_text(rendered)

    def _write_config_in_container(self, workdir: str, config_files: dict) -> None:
        vscode_dir = f"/{workdir.strip('/')}/.vscode"
        for filename, content in config_files.items():
            rendered = json.dumps(content, indent=4, sort_keys=True)
            target = f"{vscode_dir}/{filename}.json"
            # A quoted heredoc: `compose exec` takes no stdin, and the content is JSON full of
            # quotes and newlines, so it cannot ride as an argument. 'FMEOF' quoted stops the
            # shell expanding anything inside.
            script = f"mkdir -p {shlex.quote(vscode_dir)} && cat > {shlex.quote(target)} <<'FMEOF'\n{rendered}\nFMEOF\n"
            try:
                self.docker_client.compose.exec(
                    service="frappe",
                    command=f"bash -c {shlex.quote(script)}",
                    user="frappe",
                    stream=False,
                )
            except DockerException:
                self.logger.error(f"vscode config write failed: {capture_and_format_exception()}")
                self.output.warning(f"Could not write {filename}.json into the container")

    def _backup_config_file(self, file_path: Path) -> None:
        """Backup existing config file."""
        backup_path = file_path.parent / f"{file_path.stem}.{datetime.now().strftime('%d-%b-%y--%H-%M-%S')}.json"
        shutil.copy2(file_path, backup_path)
        self.output.print(f"Backup previous '{file_path.name}' : {backup_path}")

    def _install_ruff(self) -> None:
        """Install ruff in the container environment."""
        try:
            # stream=False on purpose: a discarded stream=True iterator is lazy and never
            # executes, so this install silently did nothing (and the handler below was dead).
            # Guarded by a `test -x`: this ran a pip install, and so a PyPI round trip, on every
            # single --debugger invocation even when ruff was already sitting in the venv.
            probe = f"test -x {CONTAINER_BENCH_DIR}/env/bin/ruff || {CONTAINER_BENCH_DIR}/env/bin/pip install ruff"
            self.docker_client.compose.exec(
                service="frappe",
                command=f"bash -c {shlex.quote(probe)}",
                user="frappe",
                stream=False,
            )
        except DockerException as e:
            self.logger.error(f"ruff installation exception: {capture_and_format_exception()}")
            self.output.warning("Not able to install ruff in env")

    def _attach_to_container(self, vscode_cmd: str) -> None:
        """Attach to the container using VS Code."""
        self.output.change_head("Attaching to Container")
        output = subprocess.run(vscode_cmd, shell=True)

        if output.returncode != 0:
            raise BenchAttachTocontainerFailed(self.bench_name, "frappe")
