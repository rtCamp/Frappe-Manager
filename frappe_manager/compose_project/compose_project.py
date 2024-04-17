import json
from typing import List
from rich.text import Text
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.display_manager.DisplayManager import richprint

class ComposeProject:
    def __init__(self, compose_file_manager: ComposeFile, verbose: bool = False):
        self.compose_file_manager: ComposeFile = compose_file_manager
        self.docker: DockerClient = DockerClient(compose_file_path=self.compose_file_manager.compose_path)
        self.quiet = not verbose

    def start_service(self, services: List[str] = [], force_recreate: bool = False) -> bool:
        """
        Starts the specific compose service.

        Returns:
            bool: True if the containers were started successfully, False otherwise.
        """
        status_text = f"Starting Docker Compose services {' '.join(services)}"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.up(services=services, detach=True, pull="never", force_recreate=force_recreate, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.error(f"{status_text}: Failed", exception=e)
        return True

    def stop_service(self, services: List[str] = [], timeout: int = 100) -> bool:
        """
        Stops the specific compose service.

        Returns:
            bool: True if the site is successfully stopped, False otherwise.
        """
        status_text = "Stopping Containers"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.stop(services=services,timeout = timeout, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.error(f"{status_text}: Failed", exception=e)

        return True

    def down_service(self, remove_ophans=True, volumes=True, timeout=5) -> bool:
        """
        Stops and removes the containers for the site.

        Args:
            remove_ophans (bool, optional): Whether to remove orphan containers. Defaults to True.
            volumes (bool, optional): Whether to remove volumes. Defaults to True.
            timeout (int, optional): Timeout in seconds for stopping the containers. Defaults to 5.

        Returns:
            bool: True if the containers were successfully stopped and removed, False otherwise.
        """
        status_text = "Removing containers"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.down(
                remove_orphans=remove_ophans,
                volumes=volumes,
                timeout=timeout,
                stream=True,
            )
            richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.error(f"{status_text}: Failed", exception=e)

    def pull_images(self):
        """
        Pull docker images.
        """
        status_text = "Pulling images"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.pull(stream=self.quiet)
            richprint.stdout.clear_live()
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.warning(f"{status_text}: Failed")
            raise e


    def logs(self, service: str, follow: bool = False):
        """
        Retrieve and print the logs for a specific service.

        Args:
            service (str): The name of the service.
            follow (bool, optional): Whether to continuously follow the logs. Defaults to False.
        """
        output = self.docker.compose.logs(services=[service], no_log_prefix=True, follow=follow, stream=True)
        for source, line in output:
            line = Text.from_ansi(line.decode())
            if source == "stdout":
                richprint.stdout.print(line)

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
        services = self.compose_file_manager.get_services_list()
        containers = self.compose_file_manager.get_container_names().values()
        services_status = {}
        try:
            output = self.docker.compose.ps(service=services, format="json", all=True, stream=True)
            status: list = []
            for source, line in output:
                if source == "stdout":
                    current_status = json.loads(line.decode())
                    if type(current_status) == dict:
                        status.append(current_status)
                    else:
                        status += current_status

            # this is done to exclude docker runs using docker compose run command
            for container in status:
                if container["Name"] in containers:
                    services_status[container["Service"]] = container["State"]
            return services_status
        except DockerException as e:
            return {}

    def get_host_port_binds(self):
        """
        Get the list of published ports on the host for all containers.

        Returns:
            list: A list of published ports on the host.
        """
        try:
            output = self.docker.compose.ps(all=True, format="json", stream=True)
            status: list = []
            for source, line in output:
                if source == "stdout":
                    current_status = json.loads(line.decode())
                    if type(current_status) == dict:
                        status.append(current_status)
                    else:
                        status += current_status

            ports_info = []
            for container in status:
                try:
                    port_info = container["Publishers"]
                    if port_info:
                        ports_info = ports_info + port_info
                except KeyError as e:
                    pass

            published_ports = set()
            for port in ports_info:
                try:
                    published_port = port["PublishedPort"]
                    if published_port > 0:
                        published_ports.add(published_port)
                except KeyError as e:
                    pass
            return list(published_ports)

        except DockerException as e:
            return []

    def is_service_running(self, service):
        running_status = self.get_services_running_status()
        try:
            if running_status[service] == "running":
                return True
            else:
                return False
        except KeyError:
            return False

    def restart_service(self, services: List[str] = []):
        status_text = f"Restarting service {services}"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.restart(services=services, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.error(f"{status_text}: Failed", e)
