import json
import os
import copy
from pathlib import Path
import tomlkit
from frappe_manager.compose_manager import DockerVolumeMount
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.version import Version
from frappe_manager import CLI_DIR, CLI_SERVICES_DIRECTORY
from frappe_manager.utils.helpers import get_container_name_prefix
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.migration_manager.backup_manager import BackupManager


class MigrationV0130(MigrationBase):
    version = Version("0.13.0")

    def init(self):
        self.cli_dir: Path = Path.home() / 'frappe'
        self.benches_dir = self.cli_dir / "sites"
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager(
            services_path=self.cli_dir / 'services'
        )

    def migrate_services(self):
        # remove version from services yml
        try:
            del self.services_manager.compose_project.compose_file_manager.yml['version']
        except KeyError:
            self.logger.warning("[services]: 'version' attribute not found in compose file.")
            pass

        # include new volume info
        services_volume_list = self.services_manager.compose_project.compose_file_manager.get_service_volumes(
            'global-nginx-proxy'
        )
        host_dir = CLI_SERVICES_DIRECTORY / 'nginx-proxy' / 'ssl'

        host_dir.mkdir(exist_ok=True, parents=True)

        container_dir = '/usr/share/nginx/ssl'

        ssl_volume = DockerVolumeMount(
            host=host_dir,
            container=container_dir,
            type='bind',
            compose_path=self.services_manager.compose_project.compose_file_manager.compose_path,
        )
        services_volume_list.append(ssl_volume)

        self.services_manager.compose_project.compose_file_manager.set_service_volumes(
            'global-nginx-proxy', volumes=services_volume_list
        )
        self.services_manager.compose_project.compose_file_manager.set_version(self.version.version_string())
        self.services_manager.compose_project.compose_file_manager.write_to_file()

        if self.services_manager.compose_project.is_service_running('global-nginx-proxy'):
            self.services_manager.compose_project.docker.compose.up(services=['global-nginx-proxy'])

        # rename main config
        fm_config_path = CLI_DIR / 'fm_config.toml'
        old_fm_config_path = CLI_DIR / '.fm.toml'

        if old_fm_config_path.exists():
            old_fm_config_path.rename(fm_config_path)

    def migrate_bench(self, bench: MigrationBench):
        bench_common_site_config = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        common_site_config_json = json.loads(bench_common_site_config.read_bytes())

        if 'mail_server' in common_site_config_json:
            common_site_config_json['mail_server'] = get_container_name_prefix(bench.name)
            bench_common_site_config.write_text(json.dumps(common_site_config_json))

        bench.compose_project.down_service(volumes=False)
        self.migrate_bench_compose(bench)

    def undo_bench_migrate(self, bench: MigrationBench):
        richprint.change_head("Removing Admin Tools compose file")

        admin_tools_compose_path = bench.path / 'docker-compose.admin-tools.yml'

        if admin_tools_compose_path.exists():
            admin_tools_compose_path.unlink()

        richprint.print("Removed Admin Tools compose file")

    def migrate_bench_compose(self, bench: MigrationBench):
        richprint.change_head("Migrating bench compose")

        if not bench.compose_project.compose_file_manager.exists():
            richprint.print(f"Failed to migrate {bench.name} compose file.")
            raise MigrationExceptionInBench(f"{bench.compose_project.compose_file_manager.compose_path} not found.")

        # get all the payloads
        envs = bench.compose_project.compose_file_manager.get_all_envs()

        # add HSTS=off envionment variable for all the benches
        envs["nginx"]["HSTS"] = 'off'

        if 'ENABLE_SSL' in envs['nginx']:
            try:
                del envs['nginx']['ENABLE_SSL']
            except KeyError:
                self.logger.warning(f"{bench.name} 'ENABLE_SSL' nginx's env not found.")
                pass

        # create new html in configs/nginx/html compose directory
        html_nginx_configs_path = bench.path / 'configs' / 'nginx' / 'html'
        html_nginx_configs_path.mkdir(parents=True, exist_ok=True)

        bench_config_path = bench.path / 'bench_config.toml'

        # create bench config
        frappe = envs.get('frappe', {})

        apps_list = frappe.get('APPS_LIST', None)

        if apps_list:
            apps_list = apps_list.split(',')
        else:
            apps_list = []

        developer_mode = frappe.get('DEVELOPER_MODE', True)
        name = frappe.get('SITENAME', bench.name)

        bench_config = tomlkit.document()

        bench_config['name'] = name
        bench_config['developer_mode'] = developer_mode
        bench_config['admin_tools'] = True
        bench_config['environment_type'] = 'dev'

        with open(bench_config_path, 'w') as f:
            f.write(tomlkit.dumps(bench_config))

        images_info = bench.compose_project.compose_file_manager.get_all_images()

        # change image frappe, socketio, schedule
        self.frappe_image_info = images_info['frappe']
        nginx_image_info = images_info['nginx']
        nginx_image_info['tag'] = self.version.version_string()
        self.frappe_image_info['tag'] = self.version.version_string()

        # setting all images
        images_info['frappe'] = self.frappe_image_info
        images_info['socketio'] = self.frappe_image_info
        images_info['schedule'] = self.frappe_image_info
        images_info['nginx'] = nginx_image_info

        for image in [self.frappe_image_info, nginx_image_info]:
            pull_image = f"{image['name']}:{image['tag']}"
            richprint.change_head(f"Pulling Image {pull_image}")
            output = DockerClient().pull(container_name=pull_image, stream=True)
            richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"Image pulled [blue]{pull_image}[/blue]")

        self.migrate_admin_tools_compose(bench)

        # remove adminer and mailhog from main bench compose
        del bench.compose_project.compose_file_manager.yml['services']['adminer']

        del bench.compose_project.compose_file_manager.yml['services']['mailhog']

        # remove version from compose
        try:
            del bench.compose_project.compose_file_manager.yml['version']
        except KeyError:
            self.logger.warning(f"{bench.name} 'version' attribute not found in compose file.")
            pass

        # include new volume info
        bench_volume_list = bench.compose_project.compose_file_manager.get_service_volumes('nginx')
        container_dir = '/usr/share/nginx/html'
        ssl_volume = DockerVolumeMount(
            host=html_nginx_configs_path,
            container=container_dir,
            type='bind',
            compose_path=bench.compose_project.compose_file_manager.compose_path,
        )
        bench_volume_list.append(ssl_volume)

        bench.compose_project.compose_file_manager.set_service_volumes('nginx', volumes=bench_volume_list)

        bench.compose_project.compose_file_manager.set_all_images(images_info)

        bench.compose_project.compose_file_manager.set_all_envs(envs)
        bench.compose_project.compose_file_manager.set_version(str(self.version))
        bench.compose_project.compose_file_manager.write_to_file()

        richprint.print(f"Migrated [blue]{bench.name}[/blue] compose file.")
        self.migrate_workers_compose(bench)

    def migrate_workers_compose(self, bench: MigrationBench):
        if bench.workers_compose_project.compose_file_manager.compose_path.exists():
            richprint.print("Migrating workers compose")

            workers_image_info = bench.workers_compose_project.compose_file_manager.get_all_images()
            for worker in workers_image_info.keys():
                workers_image_info[worker] = self.frappe_image_info

            try:
                del bench.workers_compose_project.compose_file_manager.yml['version']
            except KeyError:
                self.logger.warning(f"{bench.name} workers 'version' attribute not found in compose file.")
                pass

            bench.workers_compose_project.compose_file_manager.set_top_networks_name(
                "site-network", get_container_name_prefix(bench.name)
            )
            bench.workers_compose_project.compose_file_manager.set_container_names(
                get_container_name_prefix(bench.name)
            )
            bench.compose_project.compose_file_manager.set_version(str(self.version))
            bench.workers_compose_project.compose_file_manager.write_to_file()

            richprint.print(f"Migrated [blue]{bench.name}[/blue] workers compose file.")

    def migrate_admin_tools_compose(self, bench: MigrationBench):
        richprint.change_head("Create Admin Tools")

        bench_compose_yml = copy.deepcopy(bench.compose_project.compose_file_manager.yml)

        adminer_compose_service_config = bench_compose_yml['services']['adminer']
        mailhog_compose_service_config = bench_compose_yml['services']['mailhog']

        network_compose_config = bench_compose_yml.get('networks', {})

        if 'global-frontend-network' in network_compose_config:
            try:
                del network_compose_config['global-frontend-network']
            except KeyError:
                pass

        admin_tools_compose = {}
        admin_tools_compose['services'] = {
            "mailhog": mailhog_compose_service_config,
            "adminer": adminer_compose_service_config,
        }

        admin_tools_compose['networks'] = network_compose_config

        # this should run before bench compose migrate or in betwen since this depends on i
        admin_tools_compose_path = bench.path / 'docker-compose.admin-tools.yml'

        # handle admin tool migration
        admin_tools_compose_file_manager = ComposeFile(
            admin_tools_compose_path, template_name='docker-compose.admin-tools.tmpl'
        )

        admin_tools_compose_file_manager.yml = admin_tools_compose
        admin_tools_compose_file_manager.set_version(self.version.version_string())
        admin_tools_compose_file_manager.write_to_file()

        # create custom nginx directory for adming tool location config

        nginx_conf_dir = bench.path / 'configs' / 'nginx' / 'conf'
        if nginx_conf_dir.exists():
            nginx_custom_dir = nginx_conf_dir / 'custom'
            nginx_custom_dir.mkdir(parents=True, exist_ok=True)

        # change mailhog config in common_site_confiig.json
        common_bench_config_path = bench.path / "workspace/frappe-bench/sites/common_site_config.json"

        current_common_site_config = json.loads(common_bench_config_path.read_text())

        new_conf = {
            "mail_port": 1025,
            "mail_server": f"{get_container_name_prefix(bench.name)}-mailhog",
            "disable_mail_smtp_authentication": 1,
        }

        for key, value in new_conf.items():
            current_common_site_config[key] = value

        common_bench_config_path.write_text(json.dumps(current_common_site_config))

        richprint.change_head(f"Created {bench.name} Admin Tools.")
