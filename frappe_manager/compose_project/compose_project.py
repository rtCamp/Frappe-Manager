from frappe_manager.compose_project.exceptions import DockerComposeProjectFailedToRemoveError
from frappe_manager.docker import DOCKER_LINE_NOISE, ComposeFile, DockerClient, DockerComposeWrapper, DockerException
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler


class ComposeProject:
    def __init__(
        self,
        compose_file_manager: ComposeFile,
        verbose: bool = False,
        output_handler: OutputHandler | None = None,
    ):
        self.compose_file_manager: ComposeFile = compose_file_manager
        self.output = output_handler or RichOutputHandler()
        self.docker: DockerClient = DockerClient(
            compose_file_path=self.compose_file_manager.compose_path, output=self.output
        )

        assert self.docker.compose is not None, "DockerClient.compose must be initialized with compose_file_path"

    @property
    def compose(self) -> DockerComposeWrapper:
        assert self.docker.compose is not None
        return self.docker.compose

    @property
    def running(self) -> bool:
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
        services = self.compose_file_manager.get_services_list()
        containers = self.compose_file_manager.get_container_names().values()

        try:
            all_statuses = self.compose.get_all_services_status()
            return {status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers}
        except DockerException:
            return {}

    def down_service(self, remove_ophans=True, volumes=True, timeout=5):
        try:
            output = self.compose.down(
                remove_orphans=remove_ophans,
                volumes=volumes,
                timeout=timeout,
                stream=True,
            )
            self.output.live_lines(output, padding=(0, 0, 0, 2), line_filters=DOCKER_LINE_NOISE)
        except DockerException as e:
            raise DockerComposeProjectFailedToRemoveError(
                self.compose_file_manager.compose_path,
                self.compose_file_manager.get_services_list(),
            )
