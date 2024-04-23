from typing import List
from frappe_manager.compose_manager import DockerVolumeMount, DockerVolumeType
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import create_class_from_dict


class NginxProxyManager:
    def __init__(
        self,
        service_name: str,
        compose_project: ComposeProject,
    ):
        self.service_name = service_name
        self.compose_project = compose_project
        self.dirs = self._get_docker_volume_dirs()

    def _get_docker_volume_dirs(self):
        all_volumes: List[DockerVolumeMount] = self.compose_project.compose_file_manager.get_service_volumes(
            self.service_name
        )
        dirs = {}
        for volume in all_volumes:
            if volume.type == DockerVolumeType.bind:
                name = str(volume.host.name)
                dirs[name] = volume
        dirs_class = create_class_from_dict('dirs', dirs)
        return dirs_class()

    def reload(self):
        richprint.change_head("Reloading nginx")

        if self.compose_project.running:
            output = self.compose_project.docker.compose.exec(
                service=self.service_name, command='nginx -s reload', stream=False
            )
            richprint.print("Reloaded nginx.")

    def restart(self):
        richprint.change_head("Restarting nginx")

        if self.compose_project.running:
            output = self.compose_project.docker.compose.restart(services=[self.service_name], stream=False)
            richprint.print("Restarting nginx.")
