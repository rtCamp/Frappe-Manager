"""
Controls nginx process operations (reload/restart).

This module separates nginx control operations from configuration reading,
following the Single Responsibility Principle and improving testability.
"""

from frappe_manager.docker import ComposeFile, DockerClient
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler


class NginxController:
    """
    Controls nginx process operations.

    This class is responsible only for controlling the nginx process
    (reload, restart). It does not handle configuration or path management.

    Attributes:
        service_name: Name of the nginx service in docker-compose
        compose_file_manager: The compose file manager
        docker_client: The docker client for operations
    """

    def __init__(
        self,
        service_name: str,
        compose_file_manager: ComposeFile,
        docker_client: DockerClient,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize the nginx controller.

        Args:
            service_name: Name of the nginx service (e.g., 'nginx', 'nginx-proxy')
            compose_file_manager: The compose file manager
            docker_client: The docker client for operations
            output_handler: Optional output handler for display operations
        """
        self.service_name = service_name
        self.compose_file_manager = compose_file_manager
        self.docker_client = docker_client
        self.output = output_handler or RichOutputHandler()

    def reload(self):
        """
        Reload nginx configuration without stopping the service.

        This sends a SIGHUP signal to nginx, causing it to reload its
        configuration files without interrupting active connections.
        """
        self.output.change_head("Reloading nginx")

        services = self.compose_file_manager.get_services_list()
        containers = self.compose_file_manager.get_container_names().values()
        all_statuses = self.docker_client.compose.get_all_services_status()
        running_statuses = {
            status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
        }
        all_running = all(running_statuses.get(s) == "running" for s in services)

        if all_running:
            output = self.docker_client.compose.exec(service=self.service_name, command="nginx -s reload", stream=False)
            self.output.print("Reloaded nginx.")

    def restart(self):
        """
        Restart the nginx service.

        This completely stops and starts the nginx container, which will
        interrupt active connections but ensures a clean restart.
        """
        self.output.change_head("Restarting nginx")

        services = self.compose_file_manager.get_services_list()
        containers = self.compose_file_manager.get_container_names().values()
        all_statuses = self.docker_client.compose.get_all_services_status()
        running_statuses = {
            status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
        }
        all_running = all(running_statuses.get(s) == "running" for s in services)

        if all_running:
            output = self.docker_client.compose.restart(services=[self.service_name], stream=False)
            self.output.print("Restarting nginx.")
