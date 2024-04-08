from rich import inspect
from typing import List
from pathlib import Path
from frappe_manager.compose_manager import DockerVolumeMount, DockerVolumeType
from frappe_manager.display_manager.DisplayManager import richprint

from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.utils.helpers import create_class_from_dict

NGINX_LOCATION = ("""
location ^~ /.well-known/acme-challenge/ {
    auth_basic off;
    auth_request off;
    allow all;
    root /usr/share/nginx/html;
    try_files $uri =404;
    break;
}
""")

class NginxProxyManager:

    def __init__(self, services: ServicesManager, start_header = "## Start of configuration add by FM", end_header = '## End of configuration add by FM'):

        self.services = services
        self.root_dir = services.path / 'nginx-proxy'

        # nginx-proxy sub dirs
        all_volumes: List[DockerVolumeMount] = services.composefile.get_service_volumes('global-nginx-proxy')
        dirs = {}
        for volume in all_volumes:
            if volume.type == DockerVolumeType.bind:
                name = str(volume.host.name)
                dirs[name] = volume

        dirs_class = create_class_from_dict('dirs',dirs)
        self.dirs = dirs_class()

        inspect(self.dirs)

        self.start_header = start_header
        self.end_header = end_header

    def _get_container_dirs(self):
        current_volumens = self.services.composefile.get_all_volumes()
        inspect(current_volumens)

    def ascending_wildcard_locations(self,domain):
        parts = domain.split('.')
        for i in range(len(parts) - 2):
            yield f"*." + '.'.join(parts[i+1:])

    def descending_wildcard_locations(self,domain):
        parts = domain.split('.')
        for i in range(len(parts) - 1, 0, -1):
            yield '.'.join(parts[:i]) + ".*"

    def enumerate_wildcard_locations(self, domain):
        yield from self.ascending_wildcard_locations(domain)
        yield from self.descending_wildcard_locations(domain)

    def add_location_configuration(self, domain, force=False):

        domain_path: Path = self.dirs.vhostd.host / domain

        if not domain_path.is_file():
            for wildcard_domain in self.enumerate_wildcard_locations(domain):
                if Path(self.dirs.vhostd.host/wildcard_domain).is_file():
                    domain = wildcard_domain
                    break

        if domain_path.is_file():
            if self.start_header in domain_path.read_text() and self.end_header in domain_path.read_text():
                richprint.print("Location config already exits")
                if not force:
                    return True

        self._check_and_remove_location_configuration(domain_path)

        with domain_path.with_suffix('.new').open('w') as f:
            f.write(self.start_header + "\n")
            f.write(NGINX_LOCATION + "\n")
            f.write(self.end_header + "\n")

            if domain_path.is_file():
                f.write(domain_path.read_text())

        # Replace the old file with the new one
        domain_path.with_suffix('.new').replace(domain_path)

        return True

    def add_standalone_configuration(self, domain):
        server_name = f'server_name ${domain}'
        if server_name in open("/etc/nginx/conf.d/*.conf").read():
            self.add_location_configuration(domain)
        else:
            with open(f"/etc/nginx/conf.d/standalone-cert-{domain}.conf", 'w') as f:
                f.write(f"""
                    server {{
                        server_name {domain};
                        listen 80;
                        access_log /var/log/nginx/access.log vhost;
                        location ^~ /.well-known/acme-challenge/ {{
                            auth_basic off;
                            auth_request off;
                            allow all;
                            root /usr/share/nginx/html;
                            try_files $uri =404;
                            break;
                        }}
                    }}
                    """)

    def _check_and_remove_location_configuration(self,config_file_path):
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

    # def start_fake_container(self, domains, remove_tiemout = 200):
    #     container_name = generate_random_text(10)
    #     env = f'VIRTUAL_HOST={",".join(domains)}'
    #     docker = DockerClient()

    #     try:
    #         output = docker.run(
    #             image='nginx:latest',
    #             name=container_name,
    #             detach=True,
    #             stream=True,
    #             env=[env]
    #             entrypoint='bash',
    #             command=f"-c 'sleep {remove_tiemout}'",
    #             stream_only_exit_code=True,
    #         )
    #     except DockerException as e:
    #         richprint.error("Not able to start temporary container for https.",e)

    #     return container_name

    # def kill_fake_container(self, container_name, error_out: bool = True):
    #     docker = DockerClient()
    #     try:
    #         output = docker.rm(
    #             container=container_name, force=True, stream=True, stream_only_exit_code=True
    #         )
    #     except DockerException as e:
    #         if error_out:
    #             richprint.error('Not able to kill container.')
