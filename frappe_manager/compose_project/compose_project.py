
from rich.text import Text

from frappe_manager.compose_project.exceptions import (
    DockerComposeProjectFailedToPullImagesError,
    DockerComposeProjectFailedToRemoveError,
    DockerComposeProjectFailedToRestartError,
    DockerComposeProjectFailedToStartError,
    DockerComposeProjectFailedToStopError,
)
from frappe_manager.docker import ComposeFile, DockerClient, DockerComposeWrapper, DockerException
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler


class ComposeProject:
    def __init__(
        self, compose_file_manager: ComposeFile, verbose: bool = False, output_handler: OutputHandler | None = None,
    ):
        self.compose_file_manager: ComposeFile = compose_file_manager
        self.docker: DockerClient = DockerClient(compose_file_path=self.compose_file_manager.compose_path)
        self.quiet = not verbose
        self.output = output_handler or RichOutputHandler()

        # Type assertion: compose is always initialized when compose_file_path is provided
        assert self.docker.compose is not None, "DockerClient.compose must be initialized with compose_file_path"

    @property
    def compose(self) -> DockerComposeWrapper:
        """Get the docker compose wrapper with proper type narrowing."""
        assert self.docker.compose is not None
        return self.docker.compose

    def _handle_stream_output(self, output):
        """Helper to handle streaming output if in quiet mode."""
        if self.quiet:
            self.output.live_lines(output, padding=(0, 0, 0, 2))

    def start_service(self, services: list[str] = [], force_recreate: bool = False):
        """
        Starts the specific compose service.
        """
        try:
            output = self.compose.up(
                services=services, detach=True, pull="never", force_recreate=force_recreate, stream=self.quiet,
            )
            self._handle_stream_output(output)
        except DockerException as e:
            raise DockerComposeProjectFailedToStartError(self.compose_file_manager.compose_path, services)

    def stop_service(self, services: list[str] = [], timeout: int = 100):
        """
        Stops the specific compose service.
        """
        try:
            output = self.compose.stop(services=services, timeout=timeout, stream=self.quiet)
            self._handle_stream_output(output)
        except DockerException as e:
            raise DockerComposeProjectFailedToStopError(
                self.compose_file_manager.compose_path, self.compose_file_manager.get_services_list(),
            )

    def down_service(self, remove_ophans=True, volumes=True, timeout=5):
        """
        Stops and removes the containers for the site.

        Args:
            remove_ophans (bool, optional): Whether to remove orphan containers. Defaults to True.
            volumes (bool, optional): Whether to remove volumes. Defaults to True.
            timeout (int, optional): Timeout in seconds for stopping the containers. Defaults to 5.
        """
        try:
            output = self.compose.down(
                remove_orphans=remove_ophans,
                volumes=volumes,
                timeout=timeout,
                stream=True,
            )
            self.output.live_lines(output, padding=(0, 0, 0, 2))
        except DockerException as e:
            raise DockerComposeProjectFailedToRemoveError(
                self.compose_file_manager.compose_path, self.compose_file_manager.get_services_list(),
            )

    def pull_images(self):
        """
        Pull docker images.
        """
        try:
            output = self.compose.pull(stream=self.quiet)
            self._handle_stream_output(output)
        except DockerException as e:
            raise DockerComposeProjectFailedToPullImagesError(
                self.compose_file_manager.compose_path, self.compose_file_manager.get_services_list(),
            )

    def logs(self, service: str, follow: bool = False):
        """
        Retrieve and print the logs for a specific service.

        Args:
            service (str): The name of the service.
            follow (bool, optional): Whether to continuously follow the logs. Defaults to False.
        """
        output = self.compose.logs(services=[service], no_log_prefix=True, follow=follow, stream=True)
        for source, line in output:
            line = Text.from_ansi(line.decode())
            if source == "stdout":
                self.output.print(str(line))

    @property
    def running(self) -> bool:
        """
        Check if all services specified in the compose file are running.

        Returns:
            bool: True if all services are running, False otherwise.
        """
        services = self.compose_file_manager.get_services_list()
        running_status = self.get_services_running_status()

        if not running_status:
            return False

        for service in services:
            try:
                if not running_status[service] == "running":
                    return False
            except KeyError:
                return False
        return True

    def get_services_running_status(self) -> dict:
        """
        Get the running status of services in the Docker Compose file.

        Returns:
            A dictionary containing the running status of services.
            The keys are the service names, and the values are the container states.
        """
        # Use new API method which handles JSON parsing and filtering
        services = self.compose_file_manager.get_services_list()
        containers = self.compose_file_manager.get_container_names().values()

        try:
            all_statuses = self.compose.get_all_services_status()
            # Filter to only include our managed containers
            return {status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers}
        except DockerException:
            return {}

    def get_host_port_binds(self):
        """
        Get the list of published ports on the host for all containers.

        Returns:
            list: A list of published ports on the host.
        """
        try:
            # Use get_all_services_status() which already parses JSON
            all_statuses = self.compose.get_all_services_status()

            # Extract all published ports from container publishers
            published_ports = set()
            for status in all_statuses:
                publishers = status.get("Publishers", [])
                if publishers:
                    for port_info in publishers:
                        published_port = port_info.get("PublishedPort", 0)
                        if published_port > 0:
                            published_ports.add(published_port)

            return list(published_ports)
        except DockerException:
            return []

    def is_service_running(self, service):
        """
        Check if a specific service is running.

        Args:
            service: Name of the service to check

        Returns:
            bool: True if service is running, False otherwise
        """
        # Use new API convenience method
        return self.compose.is_service_running(service)

    def restart_service(self, services: list[str] = []):
        """
        Restart specific compose services.

        Args:
            services: List of service names to restart
        """
        try:
            output = self.compose.restart(services=services, stream=self.quiet)
            self._handle_stream_output(output)
        except DockerException as e:
            raise DockerComposeProjectFailedToRestartError(
                self.compose_file_manager.compose_path, self.compose_file_manager.get_services_list(),
            )
