"""
Bench Admin Tools Module

Handles admin tools (Mailpit, Adminer) management including:
- Docker compose generation and lifecycle
- Nginx location configuration
- HTTP authentication setup
- Mailpit integration with Frappe
"""

import json
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING
from frappe_manager import CLI_DEFAULT_DELIMETER
from frappe_manager.docker import ComposeFile, DockerClient
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker import DockerException
from frappe_manager.site_manager.site_exceptions import AdminToolsFailedToStart, BenchException
from frappe_manager.utils.helpers import get_container_name_prefix, get_current_fm_version, get_template_path

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Bench


class BenchAdminTools:
    def __init__(self, bench: 'Bench', nginx_proxy: Any, verbose: bool = True):
        """
        Initialize BenchAdminTools.
        
        Args:
            bench: The Bench instance
            nginx_proxy: Nginx proxy manager
            verbose: Whether to show verbose output
        """
        self.bench = bench
        self.compose_path = bench.path / "docker-compose.admin-tools.yml"
        self.bench_name = bench.name
        self.quiet = not verbose
        
        # Create ComposeFile manager for admin-tools compose file
        self.compose_file_manager = ComposeFile(
            self.compose_path, 
            template_name='docker-compose.admin-tools.tmpl'
        )
        
        # Create DockerClient with admin-tools compose file
        self.docker_client = DockerClient(compose_file_path=self.compose_path)
        
        self.nginx_proxy = nginx_proxy
        self.nginx_config_location_path: Path = self.nginx_proxy.dirs.conf.host / 'custom' / 'admin-tools.conf'
        self.http_auth_path: Path = self.nginx_proxy.dirs.conf.host / 'http_auth'

    def generate_compose(self, db_host: str):
        self.compose_file_manager.yml = self.compose_file_manager.load_template()

        # Use new configure_bench method to set all configurations atomically
        self.compose_file_manager.configure_bench(
            prefix=get_container_name_prefix(self.bench_name),
            version=get_current_fm_version(),
            envs={"adminer": {"ADMINER_DEFAULT_SERVER": db_host}},
            network_name='site-network'
        )

    def create(self, db_host: str):
        richprint.change_head("Generating admin tools configuration")
        self.generate_compose(db_host)
        richprint.print("Generating admin tools configuration: Done")

    def _generate_credentials(self) -> tuple[str, str]:
        """Generate or retrieve admin credentials"""
        import secrets
        
        # Use existing credentials from bench config or generate new ones
        username = self.bench.bench_config.admin_tools_username or "admin"
        password = self.bench.bench_config.admin_tools_password

        if not password:
            password = secrets.token_urlsafe(16)
            # Store new credentials in bench config
            self.bench.bench_config.admin_tools_username = username
            self.bench.bench_config.admin_tools_password = password
            self.bench.save_bench_config()

        return username, password

    def save_nginx_location_config(self):
        # Ensure http auth directory exists
        self.http_auth_path.mkdir(exist_ok=True)

        # Generate and save htpasswd file
        from passlib.apache import HtpasswdFile
        auth_file = self.http_auth_path / f'{self.bench_name}-admin-tools.htpasswd'

        if not self.http_auth_path.exists():
            self.http_auth_path.mkdir(exist_ok=True)

        username, password = self._generate_credentials()
        ht = HtpasswdFile(str(auth_file), new=True)
        ht.set_password(username, password)
        ht.save()

        data = {
            "mailpit_host": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}mailpit",
            "adminer_host": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}adminer",
            "auth_file": f"/etc/nginx/http_auth/{auth_file.name}",
        }

        from jinja2 import Template

        template_path: Path = get_template_path('admin-tools-location.tmpl')

        template = Template(template_path.read_text())
        output = template.render(data)

        if not self.nginx_config_location_path.parent.exists():
            self.nginx_config_location_path.mkdir(exist_ok=True)

        self.nginx_config_location_path.write_text(output)

    def remove_nginx_location_config(self):
        if self.nginx_config_location_path.exists():
            self.nginx_config_location_path.unlink()

        # Remove htpasswd file if exists
        auth_file = self.http_auth_path / f'{self.bench_name}-admin-tools.htpasswd'
        if auth_file.exists():
            auth_file.unlink()

        # Remove credentials from bench config
        self.bench.bench_config.admin_tools_username = None
        self.bench.bench_config.admin_tools_password = None
        self.bench.save_bench_config()

    def _get_common_site_config_path(self) -> Path:
        return self.compose_path.parent / "workspace/frappe-bench/sites/common_site_config.json"

    def _get_common_site_config(self) -> dict:
        config_path = self._get_common_site_config_path()
        if not config_path.exists():
            raise BenchException(self.bench_name, message='common_site_config.json not found.')
        return json.loads(config_path.read_bytes())

    def _save_common_site_config(self, config: dict):
        self._get_common_site_config_path().write_text(json.dumps(config))

    def configure_mailpit_as_default_server(self):
        richprint.change_head("Configuring Mailpit as default mail server.")
        current_common_site_config = self._get_common_site_config()

        new_conf = {
            "mail_port": 1025,
            "mail_server": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}mailpit",
            "disable_mail_smtp_authentication": 1,
        }

        for key, value in new_conf.items():
            if key not in current_common_site_config:
                current_common_site_config[key] = value

            elif not current_common_site_config[key] == value:
                current_common_site_config[key] = value

        self._save_common_site_config(current_common_site_config)
        richprint.print("Configured Mailpit as default mail server.")

    def remove_mailpit_as_default_server(self):
        richprint.change_head("Removing Mailpit as default mail server.")
        current_common_site_config = self._get_common_site_config()

        new_conf = {
            "mail_port": 1025,
            "mail_server": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}mailpit",
            "disable_mail_smtp_authentication": 1,
        }

        for key, value in new_conf.items():
            if not key in current_common_site_config:
                continue

            if not current_common_site_config[key] == value:
                continue

            del current_common_site_config[key]

        self._save_common_site_config(current_common_site_config)
        richprint.print("Removed Mailpit as default mail server.")

    def wait_till_services_started(self, interval=2, timeout=30):
        admin_tools_services = ['mailpit:8025', 'adminer:8080']

        for tool in admin_tools_services:
            running = False
            for i in range(timeout):
                try:
                    check_command = f"wait-for-it -t {interval} {get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}{tool}"
                    self.bench.docker_client.compose.exec(
                        service='nginx', command=check_command, stream=False
                    )

                    running = True
                    break
                except DockerException as e:
                    continue

            if not running:
                raise AdminToolsFailedToStart(self.bench_name)

    def enable(self, force_recreate_container: bool = False, force_configure: bool = False):
        """Enable admin tools by starting services."""
        # Use docker_client directly instead of compose_project wrapper
        try:
            output = self.docker_client.compose.up(
                services=[],
                detach=True,
                pull="never",
                force_recreate=force_recreate_container,
                stream=self.quiet
            )
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
        except DockerException as e:
            from frappe_manager.compose_project.exceptions import DockerComposeProjectFailedToStartError
            raise DockerComposeProjectFailedToStartError(self.compose_path, [])
        
        self.wait_till_services_started()
        self.save_nginx_location_config()
        self.nginx_proxy.reload()

        if force_configure:
            self.configure_mailpit_as_default_server()

    def disable(self):
        """Disable admin tools by stopping services."""
        # Use docker_client directly instead of compose_project wrapper
        try:
            output = self.docker_client.compose.stop(services=[], timeout=2, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
        except DockerException as e:
            from frappe_manager.compose_project.exceptions import DockerComposeProjectFailedToStopError
            raise DockerComposeProjectFailedToStopError(self.compose_path, self.compose_file_manager.get_services_list())
        
        self.remove_nginx_location_config()
        self.nginx_proxy.reload()

        self.remove_mailpit_as_default_server()
