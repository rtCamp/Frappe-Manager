from rich import inspect
from typing import List
from pathlib import Path
from frappe_manager.compose_manager import DockerVolumeMount, DockerVolumeType
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import create_class_from_dict, get_template_path

class NginxProxyManager:
    def __init__(self, service_name: str, compose_project: ComposeProject, start_header = "## Start of configuration add by FM", end_header = '## End of configuration add by FM'):
        self.service_name = service_name
        self.compose_project = compose_project
        self.start_header = start_header
        self.end_header = end_header
        self.dirs = self._get_docker_volume_dirs()

    def _get_docker_volume_dirs(self):
        all_volumes: List[DockerVolumeMount] = self.compose_project.compose_file_manager.get_service_volumes(self.service_name)
        dirs = {}
        for volume in all_volumes:
            if volume.type == DockerVolumeType.bind:
                name = str(volume.host.name)
                dirs[name] = volume
        dirs_class = create_class_from_dict('dirs',dirs)
        return dirs_class()

    def reload(self):
        richprint.change_head("Reloading nginx-proxy")

        if self.compose_project.running:
            output = self.compose_project.docker.compose.exec(
                service='global-nginx-proxy',
                command='nginx -s reload',
                stream=False)
        richprint.print("Reloading nginx-proxy: Done")

    def add_location_configuration(self, domain, force=False):
        domain_path: Path = self.dirs.vhostd.host / domain

        richprint.change_head("Adding nginx-proxy webroot location configuration.")
        if domain_path.is_file():
            if self.start_header in domain_path.read_text() and self.end_header in domain_path.read_text():
                if not force:
                    return True

        self._check_and_remove_location_configuration(domain_path)

        # get redirect config from template file
        nginx_certbot_redirect_config = get_template_path('redirect-certbot-nginx.conf').read_text()

        with domain_path.with_suffix('.new').open('w') as f:
            f.write(self.start_header + "\n")
            f.write(nginx_certbot_redirect_config + "\n")
            f.write(self.end_header + "\n")

            if domain_path.is_file():
                f.write(domain_path.read_text())

        # Replace the old file with the new one
        domain_path.with_suffix('.new').replace(domain_path)

        richprint.print("Configured nginx-proxy webroot location.")

        return True

    def _check_and_remove_location_configuration(self,config_file_path: Path):
            # Check if it's a file
            if config_file_path.is_file():
                # Read the content of the file
                lines = config_file_path.read_text().splitlines()

                # Start the process of checking and removing the section
                with config_file_path.open('w') as file:
                    inside_section = False
                    for line in lines:
                        if self.start_header in line:
                            inside_section = True
                        if not inside_section:
                            file.write(line + '\n')
                        if self.end_header in line:
                            inside_section = False

    def remove_all_location_configurations(self):
        for file_path in self.dirs.vhostd.host.iterdir():
            self._check_and_remove_location_configuration(file_path)

    def remove_location_config_file(self, domain):
        domain_path: Path = self.dirs.vhostd.host / domain
        if domain_path.is_file():
            domain_path.unlink()
