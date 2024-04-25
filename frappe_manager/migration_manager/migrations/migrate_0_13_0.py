import json
import os
import copy
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
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.utils.helpers import capture_and_format_exception, get_container_name_prefix
from frappe_manager.docker_wrapper.DockerClient import DockerClient


class MigrationV0130(MigrationBase):
    version = Version("0.13.0")

    def __init__(self):
        super().init()
        self.benches_dir = CLI_DIR / "sites"
        self.services_path = CLI_SERVICES_DIRECTORY
        self.benches_manager = MigrationBenches(self.benches_dir)

    def migrate_services(self):
        # backup services compose
        if not self.services_manager.compose_project.compose_file_manager.exists():
            raise MigrationExceptionInBench(
                f"Services compose at {self.services_manager.compose_project.compose_file_manager} not found."
            )

        self.backup_manager.backup(
            self.services_manager.compose_project.compose_file_manager.compose_path / "docker-compose.yml"
        )
        # remove version from services yml
        try:
            del self.services_manager.compose_project.compose_file_manager.yml['version']
        except KeyError:
            self.logger.warning(f"[services]: version attribute not found compose.")
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

    def up(self):
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

        self.services_manager: MigrationServicesManager = MigrationServicesManager()

        self.benches_manager.stop_benches()

        benches = self.benches_manager.get_all_benches()

        # migrate services
        richprint.change_head("Migrating services")
        self.migrate_services()
        richprint.print("Migrating services: Done")

        # rename main config
        fm_config_path = CLI_DIR / 'fm_config.toml'
        old_fm_config_path = CLI_DIR / '.fm.toml'
        if old_fm_config_path.exists():
            old_fm_config_path.rename(fm_config_path)

        # migrate each bench
        main_error = False

        # migrate each bench
        for bench_name, bench_path in benches.items():
            bench = MigrationBench(name=bench_name, path=bench_path.parent)

            if bench.name in self.migration_executor.migrate_benches.keys():
                bench_info = self.migration_executor.migrate_benches[bench.name]
                if bench_info['exception']:
                    richprint.print(f"Skipping migration for failed bench{bench.name}.")
                    main_error = True
                    continue

            self.migration_executor.set_bench_data(bench, migration_version=self.version)
            try:
                self.migrate_bench(bench)
            except Exception as e:
                exception_str = capture_and_format_exception()
                self.logger.error(f"{bench.name} [ EXCEPTION TRACEBACK ]:\n {exception_str}")
                richprint.update_live()
                main_error = True
                self.migration_executor.set_bench_data(bench, e, self.version)
                self.undo_bench_migrate(bench)
                bench.compose_project.down_service(volumes=False, timeout=5)

        if main_error:
            raise MigrationExceptionInBench('')

        richprint.print(f"Successfull", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

    def migrate_bench(self, bench: MigrationBench):
        richprint.print(f"Migrating bench {bench.name}", prefix=f"[bold]v{str(self.version)}:[/bold] ")

        # backup docker compose.yml
        self.backup_manager.backup(bench.path / "docker-compose.yml", bench_name=bench.name)

        # backup common_site_config.json
        bench_common_site_config = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        self.backup_manager.backup(bench_common_site_config, bench_name=bench.name)
        common_site_config_json = json.loads(bench_common_site_config.read_bytes())

        if 'mail_server' in common_site_config_json:
            common_site_config_json['mail_server'] = get_container_name_prefix(bench.name)
            bench_common_site_config.write_text(json.dumps(common_site_config_json))

        bench.compose_project.down_service(volumes=False)
        self.migrate_bench_compose(bench)

    def down(self):
        # richprint.print(f"Started",prefix=f"[ Migration v{str(self.version)} ][ROLLBACK] : ")
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)

        # undo each bench
        for bench, exception in self.migration_executor.migrate_benches.items():
            if not exception:
                self.undo_bench_migrate(bench)

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup, force=True)

        richprint.print(f"Successfull", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)

    def undo_bench_migrate(self, bench: MigrationBench):
        for backup in self.backup_manager.backups:
            if backup.bench == bench.name:
                self.backup_manager.restore(backup, force=True)

        self.logger.info(f"Undo successfull for bench: {bench.name}")

    def migrate_bench_compose(self, bench: MigrationBench):
        status_msg = "Migrating bench compose"
        richprint.change_head(status_msg)

        compose_version = bench.compose_project.compose_file_manager.get_version()

        if not bench.compose_project.compose_file_manager.exists():
            richprint.print(f"{status_msg} {compose_version} -> {self.version.version}: Failed ")
            raise MigrationExceptionInBench(f"{bench.compose_project.compose_file_manager.compose_path} not found.")

        # generate bench config for bench and save it
        status_msg = "Migrating bench compose"
        richprint.change_head(status_msg)

        # get all the payloads
        envs = bench.compose_project.compose_file_manager.get_all_envs()

        # add HSTS=off envionment variable for all the benches
        envs["nginx"]["HSTS"] = 'off'

        if 'ENABLE_SSL' in envs['nginx']:
            try:
                del envs['nginx']['ENABLE_SSL']
            except KeyError:
                self.logger.warning(f"{bench.name}: ENABLE_SSL nginx's env not found.")
                pass

        # create new html in configs/nginx/html compose directory
        html_nginx_configs_path = bench.path / 'configs' / 'nginx' / 'html'
        html_nginx_configs_path.mkdir(parents=True, exist_ok=True)

        bench_config_path = bench.path / 'bench_config.toml'

        # create bench config
        frappe = envs.get('frappe', {})

        userid = frappe.get('USERID', os.getuid())
        usergroup = frappe.get('USERGROUP', os.getgid())

        apps_list = frappe.get('APPS_LIST', None)

        if apps_list:
            apps_list = apps_list.split(',')
        else:
            apps_list = []

        frappe_branch = frappe.get('FRAPPE_BRANCH', 'version-15')
        developer_mode = frappe.get('DEVELOPER_MODE', True)
        admin_pass = frappe.get('ADMIN_PASS', 'admin')
        name = frappe.get('SITENAME', bench.name)
        mariadb_host = frappe.get('MARIADB_HOST', 'global-db')
        mariadb_root_pass = frappe.get('MARIADB_ROOT_PASS', '/run/secrets/db_root_password')
        environment_type = frappe.get('ENVIRONMENT', FMBenchEnvType.dev)
        ssl_certificate = SSLCertificate(domain=bench.name, ssl_type=SUPPORTED_SSL_TYPES.none)

        # TODO Handle admin tools compose change
        bench_config = BenchConfig(
            name=name,
            userid=userid,
            usergroup=usergroup,
            apps_list=apps_list,
            frappe_branch=frappe_branch,
            developer_mode=developer_mode,
            admin_tools=True,
            admin_pass=admin_pass,
            mariadb_host=mariadb_host,
            mariadb_root_pass=mariadb_root_pass,
            environment_type=environment_type,
            root_path=bench_config_path,
            ssl=ssl_certificate,
        )

        bench_config.export_to_toml(bench_config_path)

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
            self.logger.warning(f"{bench.name}: version attribute not found compose.")
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

        bench.compose_project.compose_file_manager.set_all_images(images_info)

        richprint.print(f"{status_msg} {compose_version} -> {self.version.version}: Done")

    def migrate_workers_compose(self, bench: MigrationBench):
        if bench.workers_compose_project.compose_file_manager.compose_path.exists():
            richprint.print("Migrating workers compose")

            workers_image_info = bench.workers_compose_project.compose_file_manager.get_all_images()
            for worker in workers_image_info.keys():
                workers_image_info[worker] = self.frappe_image_info

            try:
                del bench.workers_compose_project.compose_file_manager.yml['version']
            except KeyError:
                self.logger.warning(f"{bench.name} workers: version attribute not found compose.")
                pass

            bench.workers_compose_project.compose_file_manager.set_top_networks_name(
                "site-network", get_container_name_prefix(bench.name)
            )
            bench.workers_compose_project.compose_file_manager.set_container_names(
                get_container_name_prefix(bench.name)
            )
            bench.compose_project.compose_file_manager.set_version(str(self.version))
            bench.workers_compose_project.compose_file_manager.write_to_file()
            richprint.print("Migrating workers compose: Done")

    def migrate_admin_tools_compose(self, bench: MigrationBench):
        richprint.change_head("Create admin-tools")

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

        richprint.change_head("Created Admin-tools.")
