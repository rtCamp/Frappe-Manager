from pathlib import Path
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.migration_manager.version import Version
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.services_manager.database_service_manager import (
    DatabaseServerServiceInfo,
    DatabaseServiceManager,
    MariaDBManager,
)
from frappe_manager.utils.helpers import get_container_name_prefix


class MigrationV0150(MigrationBase):
    version = Version("0.15.0")

    def init(self):
        self.cli_dir: Path = Path.home() / 'frappe'
        self.benches_dir = self.cli_dir / "sites"
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager(
            services_path=self.cli_dir / 'services'
        )
        self.pulled_images_list = []

    def migrate_services(self):
        images_info = self.services_manager.compose_project.compose_file_manager.get_all_images()
        images_info['global-nginx-proxy'] = {'name': 'jwilder/nginx-proxy', 'tag': '1.6'}

        # pulling nginx proxy image
        pull_image = f"{images_info['global-nginx-proxy']['name']}:{images_info['global-nginx-proxy']['tag']}"
        richprint.change_head(f"Pulling Image {pull_image}")
        output = DockerClient().pull(container_name=pull_image, stream=True)
        richprint.live_lines(output, padding=(0, 0, 0, 2))
        richprint.print(f"Image pulled [blue]{pull_image}[/blue]")

        richprint.change_head("Migrating services compose")

        # add ACME_HTTP_CHALLENGE_LOCATION: false env
        envs = self.services_manager.compose_project.compose_file_manager.get_all_envs()
        envs['global-nginx-proxy'] = {'ACME_HTTP_CHALLENGE_LOCATION': False}

        self.services_manager.compose_project.compose_file_manager.set_all_envs(envs)
        self.services_manager.compose_project.compose_file_manager.set_version(str(self.version))
        self.services_manager.compose_project.compose_file_manager.write_to_file()
        richprint.print("Migrated services compose")
        richprint.change_head("Restarting services")
        self.services_manager.compose_project.start_service(force_recreate=True)

        services_database_manager: DatabaseServiceManager = MariaDBManager(
            DatabaseServerServiceInfo.import_from_compose_file('global-db', self.services_manager.compose_project),
            self.services_manager.compose_project,
        )
        # wait till db starts
        services_database_manager.wait_till_db_start()

        richprint.print("Restarted services")

    def migrate_bench(self, bench: MigrationBench):
        bench.compose_project.down_service(volumes=True)
        richprint.change_head("Migrating bench compose")

        if not bench.compose_project.compose_file_manager.exists():
            richprint.print(f"Failed to migrate {bench.name} compose file.")
            raise MigrationExceptionInBench(f"{bench.compose_project.compose_file_manager.compose_path} not found.")

        images_info = bench.compose_project.compose_file_manager.get_all_images()

        frappe_image_info = images_info['frappe']
        frappe_image_info['tag'] = self.version.version_string()

        redis_image_info = images_info['redis-cache']
        redis_image_info['tag'] = '6.2-alpine'

        nginx_image_info = images_info['nginx']
        nginx_image_info['tag'] = self.version.version_string()

        # change image nginx
        images_info['nginx'] = nginx_image_info

        # change image frappe, socketio, schedule
        images_info['frappe'] = frappe_image_info
        images_info['socketio'] = frappe_image_info
        images_info['schedule'] = frappe_image_info

        # change image for redis
        images_info['redis-cache'] = redis_image_info
        images_info['redis-queue'] = redis_image_info
        images_info['redis-socketio'] = redis_image_info

        # remove default.conf file from nginx
        nginx_default_conf_path = bench.path / 'configs' / 'nginx' / 'conf' / 'conf.d' / 'default.conf'

        if nginx_default_conf_path.exists():
            nginx_default_conf_path.unlink()

        for image in [frappe_image_info, redis_image_info, nginx_image_info]:
            pull_image = f"{image['name']}:{image['tag']}"
            if pull_image not in self.pulled_images_list:
                richprint.change_head(f"Pulling Image {pull_image}")
                output = DockerClient().pull(container_name=pull_image, stream=True)
                richprint.live_lines(output, padding=(0, 0, 0, 2))
                richprint.print(f"Image pulled [blue]{pull_image}[/blue]")
                self.pulled_images_list.append(pull_image)

        bench.compose_project.compose_file_manager.set_all_images(images_info)
        bench.compose_project.compose_file_manager.set_version(str(self.version))
        bench.compose_project.compose_file_manager.write_to_file()

        self.migrate_workers_compose(bench)
        self.migrate_admin_tools_compose(bench)

    def migrate_workers_compose(self, bench: MigrationBench):
        if bench.workers_compose_project.compose_file_manager.compose_path.exists():
            richprint.change_head("Migrating workers compose")
            workers_image_info = bench.workers_compose_project.compose_file_manager.get_all_images()

            for worker in workers_image_info.keys():
                workers_image_info[worker]['tag'] = self.version.version_string()

            bench.workers_compose_project.compose_file_manager.set_top_networks_name(
                "site-network", get_container_name_prefix(bench.name)
            )
            bench.workers_compose_project.compose_file_manager.set_container_names(
                get_container_name_prefix(bench.name)
            )
            bench.workers_compose_project.compose_file_manager.set_all_images(workers_image_info)
            bench.workers_compose_project.compose_file_manager.set_version(str(self.version))
            bench.workers_compose_project.compose_file_manager.write_to_file()
            richprint.print(f"Migrated [blue]{bench.name}[/blue] workers compose file.")

    def migrate_admin_tools_compose(self, bench: MigrationBench):
        admin_tool_compose_file = bench.path / 'docker-compose.admin-tools.yml'
        if admin_tool_compose_file.exists():
            admin_tool_compose_file_manager = ComposeFile(admin_tool_compose_file, 'docker-compose.admin-tools.tmpl')
            admin_tool_compose_project = ComposeProject(admin_tool_compose_file_manager)

            richprint.change_head("Migrating admin-tools compose")

            admin_tools_image_info = admin_tool_compose_project.compose_file_manager.get_all_images()
            admin_tools_image_info['adminer']['tag'] = '4'

            for image in [admin_tools_image_info['adminer']]:
                pull_image = f"{image['name']}:{image['tag']}"

                if pull_image not in self.pulled_images_list:
                    richprint.change_head(f"Pulling Image {pull_image}")
                    output = DockerClient().pull(container_name=pull_image, stream=True)
                    richprint.live_lines(output, padding=(0, 0, 0, 2))
                    richprint.print(f"Image pulled [blue]{pull_image}[/blue]")
                    self.pulled_images_list.append(pull_image)

            admin_tool_compose_project.compose_file_manager.set_top_networks_name(
                "site-network", get_container_name_prefix(bench.name)
            )
            admin_tool_compose_project.compose_file_manager.set_all_images(admin_tools_image_info)
            admin_tool_compose_project.compose_file_manager.set_container_names(get_container_name_prefix(bench.name))
            admin_tool_compose_project.compose_file_manager.set_version(str(self.version))
            admin_tool_compose_project.compose_file_manager.write_to_file()

            richprint.print(f"Migrated [blue]{bench.name}[/blue] admin-tools compose file.")
