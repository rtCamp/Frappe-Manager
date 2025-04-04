import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from frappe_manager import CLI_DEFAULT_DELIMETER

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Bench

from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.site_manager.site_exceptions import AdminToolsFailedToStart, BenchException
from frappe_manager.ssl_manager.nginxproxymanager import NginxProxyManager
from frappe_manager.utils.helpers import get_container_name_prefix, get_current_fm_version, get_template_path


class AdminTools:
    def __init__(self, bench: 'Bench', nginx_proxy: NginxProxyManager, verbose: bool = True):
        self.bench: Bench = bench
        self.compose_path = bench.path / "docker-compose.admin-tools.yml"
        self.bench_name = bench.name
        self.quiet = not verbose
        self.compose_project = ComposeProject(
            ComposeFile(self.compose_path, template_name='docker-compose.admin-tools.tmpl')
        )
        self.nginx_proxy: NginxProxyManager = nginx_proxy
        self.nginx_config_location_path: Path = self.nginx_proxy.dirs.conf.host / 'custom' / 'admin-tools.conf'
        self.http_auth_path: Path = self.nginx_proxy.dirs.conf.host / 'http_auth'

    def generate_compose(self, db_host: str):
        self.compose_project.compose_file_manager.yml = self.compose_project.compose_file_manager.load_template()

        self.compose_project.compose_file_manager.set_envs('adminer', {"ADMINER_DEFAULT_SERVER": db_host})
        self.compose_project.compose_file_manager.set_envs(
            'rqdash',
            {
                "USERID": str(self.bench.bench_config.userid),
                "USERGROUP": str(self.bench.bench_config.usergroup),
            },
        )

        self.compose_project.compose_file_manager.set_container_names(get_container_name_prefix(self.bench_name))
        self.compose_project.compose_file_manager.set_root_volumes_names(get_container_name_prefix(self.bench_name))
        self.compose_project.compose_file_manager.set_root_networks_name(
            'site-network', get_container_name_prefix(self.bench_name)
        )
        self.compose_project.compose_file_manager.set_version(get_current_fm_version())
        self.compose_project.compose_file_manager.write_to_file()

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
            "rqdash_host": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}rqdash",
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
        admin_tools_services = ['mailpit:8025', 'adminer:8080', 'rqdash:9181']

        for tool in admin_tools_services:
            running = False
            for i in range(timeout):
                try:
                    check_command = f"wait-for-it -t {interval} {get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}{tool}"
                    self.nginx_proxy.compose_project.docker.compose.exec(
                        service='nginx', command=check_command, stream=False
                    )

                    running = True
                    break
                except DockerException as e:
                    continue

            if not running:
                raise AdminToolsFailedToStart(self.bench_name)

    def enable(self, force_recreate_container: bool = False, force_configure: bool = False):
        self.compose_project.start_service(force_recreate=force_recreate_container)
        self.wait_till_services_started()
        self.save_nginx_location_config()
        self.nginx_proxy.reload()

        if force_configure:
            self.configure_mailpit_as_default_server()

    def disable(self):
        self.compose_project.stop_service(timeout=2)
        self.remove_nginx_location_config()
        self.nginx_proxy.reload()

        self.remove_mailpit_as_default_server()
