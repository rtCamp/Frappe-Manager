"""
BenchDockerOps - Docker and Compose Operations Module

This module handles all Docker and docker-compose operations for a bench.
Extracted from the monolithic Bench class for better separation of concerns.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Optional, Literal, cast, Iterable, Iterator, Tuple

from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.docker import DockerClient, DockerException
from frappe_manager.docker.compose_file import ComposeFile
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.logger import log
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.utils.docker import host_run_cp
from frappe_manager.utils.helpers import get_container_name_prefix, get_current_fm_version
from frappe_manager import CLI_DEFAULT_DELIMETER


class BenchDockerOps:
    """Handles all Docker and compose operations for a bench."""

    def __init__(
        self,
        docker_client: DockerClient,
        compose_file_manager: ComposeFile,
        config: BenchConfig,
        path: Path,
        quiet: bool = False,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchDockerOps.

        Args:
            docker_client: Docker client for operations
            compose_file_manager: Compose file manager
            config: Bench configuration
            path: Path to bench directory
            quiet: Whether to suppress output
            output_handler: Handler for output operations
        """
        self.docker_client = docker_client
        self.compose_file_manager = compose_file_manager
        self.config = config
        self.path = path
        self.quiet = quiet
        self.output = output_handler or RichOutputHandler()
        self.logger = log.get_logger()

    def _is_service_running(self, service: str) -> bool:
        """Check if a specific service is running."""
        try:
            all_statuses = self.docker_client.compose.get_all_services_status()
            return any(status["Service"] == service and status["State"] == "running" for status in all_statuses)
        except DockerException:
            return False

    def is_running(self) -> bool:
        """Check if all bench services are running."""
        try:
            services = self.compose_file_manager.get_services_list()
            containers = self.compose_file_manager.get_container_names().values()
            all_statuses = self.docker_client.compose.get_all_services_status()
            running_statuses = {
                status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
            }
            return all(running_statuses.get(s) == "running" for s in services)
        except DockerException:
            return False

    def get_services_running_status(self) -> dict:
        """Get the running status of all services."""
        try:
            services = self.compose_file_manager.get_services_list()
            containers = self.compose_file_manager.get_container_names().values()
            all_statuses = self.docker_client.compose.get_all_services_status()
            return {status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers}
        except DockerException:
            return {}

    def generate_compose(self, inputs: dict) -> None:
        """
        Generate the compose file for the bench based on the given inputs.

        Args:
            inputs: Dictionary containing environment, labels, users, etc.
        """
        # Extract inputs
        environments = inputs.get("environment")
        labels = inputs.get("labels")
        users = None

        # Convert user format if present
        if "user" in inputs:
            users = {}
            for container_name, user_data in inputs["user"].items():
                users[container_name] = (user_data["uid"], user_data["gid"])

        # Build list of all domains for network aliases (primary + alias domains)
        network_aliases = [self.config.name]
        if self.config.alias_domains:
            network_aliases.extend(self.config.alias_domains)

        # Set network aliases separately (not part of configure_bench)
        self.compose_file_manager.set_network_alias("nginx", "site-network", network_aliases)

        # Use configure_bench method to set all configurations atomically
        self.compose_file_manager.configure_bench(
            prefix=get_container_name_prefix(network_aliases[0]),
            version=get_current_fm_version(),
            envs=environments,
            labels=labels,
            users=users,
            network_name="site-network",
        )

    def create_compose_dirs(self) -> bool:
        """
        Create the necessary directories for the Compose setup.

        Returns:
            True if directories are created successfully
        """
        self.output.change_head("Creating required directories")

        workspace_path = self.path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        frappe_bench_dir = workspace_path / "frappe-bench"
        frappe_bench_dir.mkdir(parents=True, exist_ok=True)

        (frappe_bench_dir / "sites").mkdir(parents=True, exist_ok=True)
        (frappe_bench_dir / "apps").mkdir(parents=True, exist_ok=True)
        (frappe_bench_dir / "logs").mkdir(parents=True, exist_ok=True)
        (frappe_bench_dir / "config").mkdir(parents=True, exist_ok=True)
        (frappe_bench_dir / "config" / "pids").mkdir(parents=True, exist_ok=True)

        apps_txt = frappe_bench_dir / "sites" / "apps.txt"
        if not apps_txt.exists():
            apps_txt.write_text("frappe\n")

        common_site_config = frappe_bench_dir / "sites" / "common_site_config.json"
        if not common_site_config.exists():
            common_site_config.write_text("{}")

        configs_path = self.path / "configs"
        configs_path.mkdir(parents=True, exist_ok=True)

        # create nginx dirs
        nginx_dir = configs_path / "nginx"
        nginx_dir.mkdir(parents=True, exist_ok=True)

        nginx_populate_dir = ["conf"]
        nginx_image = self.compose_file_manager.yml["services"]["nginx"]["image"]

        for directory in nginx_populate_dir:
            new_dir = nginx_dir / directory
            if not new_dir.exists():
                new_dir_abs = str(new_dir.absolute())
                host_run_cp(
                    nginx_image,
                    source="/etc/nginx",
                    destination=new_dir_abs,
                    docker=self.docker_client,
                )

        nginx_subdirs = ["logs", "cache", "run", "html"]

        for directory in nginx_subdirs:
            new_dir = nginx_dir / directory
            new_dir.mkdir(parents=True, exist_ok=True)

        # Copy prebaked Python and Node from Docker image to host workspace
        frappe_image = self.compose_file_manager.yml["services"]["frappe"]["image"]

        # Copy prebaked UV Python installations
        uv_dir = workspace_path / ".uv"
        if not uv_dir.exists():
            uv_dir_abs = str(uv_dir.absolute())
            host_run_cp(
                frappe_image,
                source="/workspace/.uv",
                destination=uv_dir_abs,
                docker=self.docker_client,
            )

        # Copy prebaked FNM Node installations
        fnm_dir = workspace_path / ".fnm"
        if not fnm_dir.exists():
            fnm_dir_abs = str(fnm_dir.absolute())
            host_run_cp(
                frappe_image,
                source="/workspace/.fnm",
                destination=fnm_dir_abs,
                docker=self.docker_client,
            )

        self.output.print("Created all required directories.")

        return True

    def start(
        self,
        services: Optional[list] = None,
        force_recreate: bool = False,
        pull: Literal["missing", "never", "always"] = "never",
    ) -> None:
        """
        Start bench services.

        Args:
            services: List of specific services to start (None for all)
            force_recreate: Force recreate containers
            pull: Pull policy (never, always, missing)
        """
        self.output.change_head("Starting bench services")

        if self.quiet:
            output = self.docker_client.compose.up(
                services=services or [], detach=True, pull=pull, force_recreate=force_recreate, stream=True
            )
            self.output.live_lines(cast(Iterator[Tuple[str, bytes]], output), padding=(0, 0, 0, 2))
        else:
            self.docker_client.compose.up(
                services=services or [], detach=True, pull=pull, force_recreate=force_recreate, stream=False
            )

        self.output.print("Started bench services.")

    def stop(self, timeout: int = 10) -> None:
        """
        Stop bench services.

        Args:
            timeout: Timeout in seconds for stopping containers
        """
        self.output.change_head("Stopping bench services")
        output = self.docker_client.compose.stop(services=[], timeout=timeout, stream=self.quiet)
        if self.quiet:
            self.output.live_lines(cast(Iterator[Tuple[str, bytes]], output), padding=(0, 0, 0, 2))
        self.output.print("Stopped bench services.")

    def remove_containers(self, remove_volumes: bool = True, timeout: int = 5) -> None:
        """
        Remove bench containers.

        Args:
            remove_volumes: Whether to remove volumes
            timeout: Timeout for removal
        """
        if self.compose_file_manager.exists():
            self.output.change_head("Removing bench containers.")
            output = self.docker_client.compose.down(
                remove_orphans=True, volumes=remove_volumes, timeout=timeout, stream=True
            )
            self.output.live_lines(cast(Iterator[Tuple[str, bytes]], output), padding=(0, 0, 0, 2))
            self.output.print("Removed bench containers.")
        else:
            self.output.warning('Bench compose file not found. Skipping containers removal.')

    def shell(
        self, compose_service: str, user: str | None = None, shell_path: str | None = None, use_run: bool = False
    ) -> None:
        """
        Spawn a shell for the specified service.

        Args:
            compose_service: The name of the service
            user: The name of the user (defaults to "frappe" for frappe service)
            shell_path: Path to shell executable (overrides auto-detection)
            use_run: Use 'docker compose run --rm' instead of 'docker compose exec'
        """
        self.output.change_head("Spawning shell")

        if compose_service == "frappe" and not user:
            user = "frappe"

        if not use_run and not self._is_service_running(compose_service):
            self.output.stop()
            self.output.display_error(f"Cannot spawn shell. Compose service '{compose_service}' not running!")
            return

        self.output.stop()

        if not shell_path:
            non_bash_supported = ["redis-cache", "redis-queue", "adminer", "mailpit"]
            shell_path = "/bin/bash" if compose_service not in non_bash_supported else "sh"

        if use_run:
            run_args: Dict[str, Any] = {
                "service": compose_service,
                "rm": True,
                "entrypoint": shell_path,
            }

            if compose_service == "frappe":
                run_args["entrypoint"] = "/usr/bin/zsh"

            if user:
                run_args["user"] = user

            try:
                self.docker_client.compose.run(**run_args)
            except DockerException as e:
                self.output.warning(f"Shell exited with error code: {e.output.exit_code}")
        else:
            exec_args: Dict[str, Any] = {"service": compose_service, "command": shell_path}

            if compose_service == "frappe":
                exec_args["command"] = "/usr/bin/zsh"
                exec_args["workdir"] = "/workspace/frappe-bench"

            if user:
                exec_args["user"] = user

            exec_args["capture_output"] = False

            try:
                self.docker_client.compose.exec(**exec_args)
            except DockerException as e:
                self.output.warning(f"Shell exited with error code: {e.output.exit_code}")

    def execute_command(
        self,
        compose_service: str,
        command: str,
        user: str | None = None,
        shell_path: str | None = None,
        use_run: bool = False,
    ) -> int:
        """
        Execute a single command in the specified service and return exit code.

        Args:
            compose_service: The name of the service
            command: The command to execute
            user: The name of the user (defaults to "frappe" for frappe service)
            shell_path: Path to shell executable (overrides auto-detection)
            use_run: Use 'docker compose run --rm' instead of 'docker compose exec'

        Returns:
            Exit code of the executed command
        """
        if compose_service == "frappe" and not user:
            user = "frappe"

        if not use_run and not self._is_service_running(compose_service):
            self.output.display_error(f"Cannot execute command. Compose service '{compose_service}' not running!")
            return 1

        if not shell_path:
            non_bash_supported = ["redis-cache", "redis-queue", "adminer", "mailpit"]
            shell_path = "/bin/bash" if compose_service not in non_bash_supported else "sh"

        if use_run:
            run_args: Dict[str, Any] = {
                "service": compose_service,
                "command": f'-c "{command}"',
                "rm": True,
                "use_shlex_split": True,
                "stream": False,
                "entrypoint": shell_path,
            }

            if user:
                run_args["user"] = user

            try:
                result = cast(SubprocessOutput, self.docker_client.compose.run(**run_args))

                if result.stdout:
                    for line in result.stdout:
                        print(line)
                if result.stderr:
                    for line in result.stderr:
                        print(line, file=sys.stderr)

                return result.exit_code
            except DockerException as e:
                if e.output.stdout:
                    for line in e.output.stdout:
                        print(line)
                if e.output.stderr:
                    for line in e.output.stderr:
                        print(line, file=sys.stderr)
                return e.output.exit_code
        else:
            exec_args: Dict[str, Any] = {
                "service": compose_service,
                "command": f'{shell_path} -c "{command}"',
                "stream": False,
                "capture_output": True,
                "use_shlex_split": True,
            }

            if compose_service == "frappe":
                exec_args["workdir"] = "/workspace/frappe-bench"

            if user:
                exec_args["user"] = user

            try:
                result = self.docker_client.compose.exec(**exec_args)

                if result.stdout:
                    for line in result.stdout:
                        print(line)
                if result.stderr:
                    for line in result.stderr:
                        print(line, file=sys.stderr)

                return result.exit_code
            except DockerException as e:
                if e.output.stdout:
                    for line in e.output.stdout:
                        print(line)
                if e.output.stderr:
                    for line in e.output.stderr:
                        print(line, file=sys.stderr)
                return e.output.exit_code

    def logs(self, services: Optional[list] = None, follow: bool = False) -> None:
        """
        Display logs for services.

        Args:
            services: List of services to show logs for (None for all)
            follow: Whether to follow logs continuously
        """
        self.output.change_head("Showing logs")

        services_list = services or []
        if services_list and not self._is_service_running(services_list[0]):
            self.output.stop()
            self.output.display_error(f"Cannot show logs. Service '{services_list[0]}' not running!")
            return

        output = self.docker_client.compose.logs(services=services_list, follow=follow, stream=True)
        self.output.live_lines(cast(Iterator[Tuple[str, bytes]], output), padding=(0, 0, 0, 2))

    def frappe_logs_till_start(self) -> None:
        """
        Retrieve and print the logs of the 'frappe' service until supervisor starts.
        """
        output = cast(
            Iterable[Tuple[str, bytes]],
            self.docker_client.compose.logs(
                services=["frappe"],
                no_log_prefix=True,
                no_color=True,
                follow=True,
                stream=True,
            ),
        )

        if self.quiet:
            self.output.live_lines(
                cast(Iterator[Tuple[str, bytes]], output),
                padding=(0, 0, 0, 2),
                stop_string="INFO supervisord started with pid",
            )
        else:
            for source, line in output:
                if not source == "exit_code":
                    line = line.decode()

                    if "Updating files:".lower() in line.lower():
                        continue
                    if "[==".lower() in line.lower():
                        print(line)
                        continue
                    # Use regular print for stdout to maintain format
                    print(line, end='')
                    if "INFO supervisord started with pid".lower() in line.lower():
                        break

    def restart_services(self, services: list) -> None:
        """
        Restart specific services.

        Args:
            services: List of service names to restart
        """
        self.output.change_head(f"Restarting services - {' '.join(services)}")
        output = self.docker_client.compose.restart(services=services, stream=self.quiet)
        if self.quiet:
            self.output.live_lines(cast(Iterator[Tuple[str, bytes]], output), padding=(0, 0, 0, 2))
        self.output.print(f"Restarted services - {' '.join(services)}")

    def exec_command(self, service: str, command: str, user: Optional[str] = None, stream: bool = False):
        """
        Execute a command in a service container.

        Args:
            service: Service name
            command: Command to execute
            user: User to run as
            stream: Whether to stream output

        Returns:
            Command output
        """
        exec_args = {'service': service, 'command': command, 'stream': stream}

        if user:
            exec_args['user'] = user

        return self.docker_client.compose.exec(**exec_args)

    def check_required_docker_images_available(self) -> None:
        """
        Check if all required Docker images are available locally.

        This method verifies that all images needed for the bench are
        present on the system before attempting to start containers.

        Raises:
            BenchOperationRequiredDockerImagesNotAvailable: If any required images are missing

        Example:
            >>> docker_ops.check_required_docker_images_available()
        """
        from frappe_manager.utils.site import get_all_docker_images
        from frappe_manager.site_manager.exceptions import BenchOperationRequiredDockerImagesNotAvailable

        self.output.change_head("Checking required docker images availability")
        fm_images = get_all_docker_images()
        system_available_images = self.docker_client.images()

        not_available_images = []

        for key, value in fm_images.items():
            name = value['name']
            tag = value['tag']

            found = False

            for item in system_available_images:
                if item.get('Repository') == name and item.get('Tag') == tag:
                    found = True
                    break

            if not found:
                image = f"{name}:{tag}"
                not_available_images.append(image)

        # Remove duplicates
        not_available_images = list(dict.fromkeys(not_available_images))

        if not_available_images:
            for image in not_available_images:
                self.output.display_error(f"Docker image '{image}' is not available locally")

            # Get bench name from config
            bench_name = self.config.container_name_prefix.replace('-', '.')
            raise BenchOperationRequiredDockerImagesNotAvailable(bench_name, 'fm self update-images')
