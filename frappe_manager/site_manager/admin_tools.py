import json
from pathlib import Path
import time
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.site_manager.site_exceptions import AdminToolsFailedToStart, BenchException
from frappe_manager.ssl_manager.nginxproxymanager import NginxProxyManager
from frappe_manager.utils.helpers import get_container_name_prefix, get_current_fm_version, get_template_path


class AdminTools:
    def __init__(self, bench_name: str, bench_path: Path, nginx_proxy: NginxProxyManager, verbose: bool = True):
        self.compose_path = bench_path / "docker-compose.admin-tools.yml"
        self.bench_name = bench_name
        self.quiet = not verbose
        self.compose_project = ComposeProject(
            ComposeFile(self.compose_path, template_name='docker-compose.admin-tools.tmpl')
        )
        self.nginx_proxy: NginxProxyManager = nginx_proxy
        self.nginx_config_location_path: Path = self.nginx_proxy.dirs.conf.host / 'custom' / 'admin-tools.conf'

    def generate_compose(self, db_host: str):
        # env set db_host for ADMINER_DEFAULT_SERVER
        env = {"ADMINER_DEFAULT_SERVER": db_host}
        self.compose_project.compose_file_manager.set_envs('adminer', env)

        self.compose_project.compose_file_manager.yml = self.compose_project.compose_file_manager.load_template()
        self.compose_project.compose_file_manager.set_container_names(get_container_name_prefix(self.bench_name))
        self.compose_project.compose_file_manager.yml["networks"]["site-network"]["name"] = (
            self.bench_name.replace(".", "") + f"-network"
        )
        self.compose_project.compose_file_manager.set_version(get_current_fm_version())
        self.compose_project.compose_file_manager.write_to_file()

    def create(self, db_host: str):
        richprint.change_head("Generating admin tools configuration")
        self.generate_compose(db_host)
        richprint.print("Generating admin tools configuration: Done")

    def save_nginx_location_config(self):
        data = {
            "mailhog_host": f"{get_container_name_prefix(self.bench_name)}-mailhog",
            "adminer_host": f"{get_container_name_prefix(self.bench_name)}-adminer",
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

    def get_common_site_config(self, common_bench_config_path: Path):
        if not common_bench_config_path.exists():
            raise BenchException(self.bench_name, message='common_site_config.json not found.')

        current_common_site_config = json.loads(common_bench_config_path.read_bytes())

        return current_common_site_config

    def configure_mailhog_as_default_server(self, force: bool = False):
        richprint.change_head("Configuring Mailhog as default mail server.")
        common_bench_config_path = self.compose_path.parent / "workspace/frappe-bench/sites/common_site_config.json"

        current_common_site_config = self.get_common_site_config(common_bench_config_path)

        new_conf = {
            "mail_port": 1025,
            "mail_server": f"{get_container_name_prefix(self.bench_name)}-mailhog",
            "disable_mail_smtp_authentication": 1,
        }

        if not force:
            if 'mail_server' in current_common_site_config:
                if not current_common_site_config['mail_server'] == new_conf['mail_server']:
                    raise BenchException(
                        self.bench_name,
                        'Failed to set MailHog as the default mail server because another mail server is currently configured as the default.',
                    )

        frappe_server_restart_required = False

        for key, value in new_conf.items():
            if key not in current_common_site_config:
                current_common_site_config[key] = value
                frappe_server_restart_required = True

            elif not current_common_site_config[key] == value:
                current_common_site_config[key] = value
                frappe_server_restart_required = True

        common_bench_config_path.write_text(json.dumps(current_common_site_config))
        richprint.print("Configured Mailhog as default mail server.")
        return frappe_server_restart_required

    def remove_mailhog_as_default_server(self):
        richprint.change_head("Removing Mailhog as default mail server.")
        common_bench_config_path = self.compose_path.parent / "workspace/frappe-bench/sites/common_site_config.json"
        current_common_site_config = self.get_common_site_config(common_bench_config_path)

        new_conf = {
            "mail_port": 1025,
            "mail_server": f"{get_container_name_prefix(self.bench_name)}-mailhog",
            "disable_mail_smtp_authentication": 1,
        }

        frappe_server_restart_required = False
        for key, value in new_conf.items():
            if not key in current_common_site_config:
                continue
            if not current_common_site_config[key] == value:
                continue
            del current_common_site_config[key]
            frappe_server_restart_required = True

        common_bench_config_path.write_text(json.dumps(current_common_site_config))
        richprint.print("Removed Mailhog as default mail server.")
        return frappe_server_restart_required

    def wait_till_services_started(self, interval=2, timeout=30):
        admin_tools_services = ['mailhog:8025', 'adminer:8080']

        for tool in admin_tools_services:
            running = False
            for i in range(timeout):
                try:
                    check_command = f"wait-for-it -t {interval} {get_container_name_prefix(self.bench_name)}-{tool}"
                    self.nginx_proxy.compose_project.docker.compose.exec(
                        service='nginx', command=check_command, stream=False
                    )

                    running = True
                    break
                except DockerException as e:
                    continue

            if not running:
                raise AdminToolsFailedToStart(self.bench_name)

    def enable(self, force_recreate_container: bool = False, force_configure: bool = False) -> bool:
        self.compose_project.start_service(force_recreate=force_recreate_container)
        self.wait_till_services_started()
        self.save_nginx_location_config()
        self.nginx_proxy.reload()
        frappe_server_restart_required = self.configure_mailhog_as_default_server(force=force_configure)
        return frappe_server_restart_required

    def disable(self) -> bool:
        self.compose_project.stop_service()
        self.remove_nginx_location_config()
        self.nginx_proxy.reload()
        frappe_server_restart_required = self.remove_mailhog_as_default_server()
        return frappe_server_restart_required
