import os
from pathlib import Path

from frappe_manager import site_manager
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import (
    MigrationExceptionInBench,
)
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.migration_manager.version import Version
from frappe_manager.services_manager.database_service_manager import (
    DatabaseServerServiceInfo,
    DatabaseServiceManager,
    MariaDBManager,
)


def get_container_name_prefix(site_name):
    return 'fm' + "__" + site_name.replace(".", "_")


def get_new_envrionment_for_service(service_name: str):
    envs = {
        "USERID": os.getuid(),
        "USERGROUP": os.getgid(),
        "SUPERVISOR_SERVICE_CONFIG_FILE_NAME": f"{service_name}.fm.supervisor.conf",
    }
    return envs


class MigrationV0167(MigrationBase):
    version = Version("0.16.7")

    def init(self):
        self.cli_dir: Path = Path.home() / "frappe"
        self.benches_dir = self.cli_dir / "sites"
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager(
            services_path=self.cli_dir / "services"
        )
        self.pulled_images_list = []

    def migrate_bench(self, bench: MigrationBench):
        bench.compose_project.down_service(volumes=True)
        richprint.change_head("Migrating bench compose")

        if not bench.compose_project.compose_file_manager.exists():
            richprint.error(f"Failed to migrate {bench.name} compose file.")
            raise MigrationExceptionInBench(f"{bench.compose_project.compose_file_manager.compose_path} not found.")

        # envs and command
        envs = bench.compose_project.compose_file_manager.get_all_envs()

        new_env_services = ['frappe', 'socketio', 'schedule']

        for service_name in new_env_services:
            if service_name not in ['frappe']:
                envs[service_name] = get_new_envrionment_for_service(service_name)
                bench.compose_project.compose_file_manager.set_service_command(
                    service_name, 'launch_supervisor_service.sh'
                )

            envs[service_name]["SERVICE_NAME"] = service_name

        images_info = bench.compose_project.compose_file_manager.get_all_images()

        # volume remove :cached
        volumes = bench.compose_project.compose_file_manager.get_all_services_volumes()
        bench.compose_project.compose_file_manager.set_all_services_volumes(volumes)

        # container names
        bench.compose_project.compose_file_manager.set_container_names(get_container_name_prefix(bench.name))

        # root volumes name
        bench.compose_project.compose_file_manager.set_root_volumes_names(get_container_name_prefix(bench.name))

        # root network names
        bench.compose_project.compose_file_manager.set_root_networks_name(
            "site-network", get_container_name_prefix(bench.name)
        )

        # images
        frappe_image_info = images_info["frappe"]
        frappe_image_info["tag"] = self.version.version_string()

        redis_image_info = images_info["redis-cache"]
        redis_image_info["tag"] = "6.2-alpine"

        nginx_image_info = images_info["nginx"]
        nginx_image_info["tag"] = 'v0.16.1'

        # change image nginx
        images_info["nginx"] = nginx_image_info

        # change image frappe, socketio, schedule
        images_info["frappe"] = frappe_image_info
        images_info["socketio"] = frappe_image_info
        images_info["schedule"] = frappe_image_info

        # change image for redis
        images_info["redis-cache"] = redis_image_info
        images_info["redis-queue"] = redis_image_info
        images_info["redis-socketio"] = redis_image_info

        # remove default.conf file from nginx
        nginx_default_conf_path: Path = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"

        if nginx_default_conf_path.exists():
            nginx_default_conf_path.unlink()

        for image in [
            frappe_image_info,
            redis_image_info,
            nginx_image_info,
            {'name': f'ghcr.io/rtcamp/frappe-manager-prebake', 'tag': self.version.version_string()},
        ]:
            pull_image = f"{image['name']}:{image['tag']}"
            if pull_image not in self.pulled_images_list:
                richprint.change_head(f"Pulling Image {pull_image}")
                output = DockerClient().pull(container_name=pull_image, stream=True)
                richprint.live_lines(output, padding=(0, 0, 0, 2))
                richprint.print(f"Image pulled [blue]{pull_image}[/blue]")
                self.pulled_images_list.append(pull_image)

        bench.compose_project.compose_file_manager.set_all_images(images_info)
        bench.compose_project.compose_file_manager.set_all_envs(envs, append=False)
        bench.compose_project.compose_file_manager.set_version(str(self.version))
        bench.compose_project.compose_file_manager.write_to_file()

        self.migrate_workers_compose(bench)
        self.migrate_admin_tools_compose(bench)

    def migrate_workers_compose(self, bench: MigrationBench):
        if bench.workers_compose_project.compose_file_manager.compose_path.exists():
            richprint.change_head("Migrating workers compose")
            workers_image_info = bench.workers_compose_project.compose_file_manager.get_all_images()

            for worker in workers_image_info.keys():
                workers_image_info[worker]["tag"] = self.version.version_string()

            # env and command
            envs = bench.workers_compose_project.compose_file_manager.get_all_envs()

            new_env_services = bench.workers_compose_project.compose_file_manager.get_services_list()

            for service_name in new_env_services:
                envs[service_name] = get_new_envrionment_for_service(service_name)
                bench.workers_compose_project.compose_file_manager.set_service_command(
                    service_name, 'launch_supervisor_service.sh'
                )
                envs[service_name]["SERVICE_NAME"] = service_name

            # volume remove :cached
            volumes = bench.workers_compose_project.compose_file_manager.get_all_services_volumes()
            bench.workers_compose_project.compose_file_manager.set_all_services_volumes(volumes)

            # site network
            bench.workers_compose_project.compose_file_manager.set_root_networks_name(
                "site-network", get_container_name_prefix(bench.name), external=True
            )

            # container names
            bench.workers_compose_project.compose_file_manager.set_container_names(
                get_container_name_prefix(bench.name)
            )
            bench.workers_compose_project.compose_file_manager.set_all_images(workers_image_info)
            bench.workers_compose_project.compose_file_manager.set_version(str(self.version))

            bench.workers_compose_project.compose_file_manager.set_all_envs(envs, append=False)
            bench.workers_compose_project.compose_file_manager.write_to_file()

            richprint.print(f"Migrated [blue]{bench.name}[/blue] workers compose file.")

    def migrate_admin_tools_compose(self, bench: MigrationBench):
        admin_tool_compose_file = bench.path / "docker-compose.admin-tools.yml"
        if admin_tool_compose_file.exists():
            admin_tool_compose_file_manager = ComposeFile(admin_tool_compose_file, "docker-compose.admin-tools.tmpl")
            admin_tool_compose_project = ComposeProject(admin_tool_compose_file_manager)

            richprint.change_head("Migrating admin-tools compose")

            # remove mailhog
            if 'mailhog' in admin_tool_compose_project.compose_file_manager.yml['services']:
                del admin_tool_compose_project.compose_file_manager.yml['services']['mailhog']

            # configure mailpit
            mailpit_dir = bench.path / 'configs' / 'mailpit' / 'data'
            mailpit_dir.mkdir(parents=True, exist_ok=True)

            admin_tool_compose_project.compose_file_manager.yml['services']['mailpit'] = {
                "image": "axllent/mailpit:v1.22",
                "volumes": ["mailpit-data:/data"],
                "expose": ['1025', '8025'],
                "environment": {
                    "MP_WEBROOT": "mailpit",
                    "MP_MAX_MESSAGES": "5000",
                    "MP_DATABASE": "/data/mailpit.db",
                    "MP_SMTP_AUTH_ACCEPT_ANY": "1",
                    "MP_SMTP_AUTH_ALLOW_INSECURE": "1",
                },
                "networks": {"site-network": None},
            }

            # add root volumes
            admin_tool_compose_project.compose_file_manager.yml['volumes'] = {
                "mailpit-data": {"name": f"{get_container_name_prefix(bench.name)}__mailpit-data"}
            }

            # Add rqdash configuration
            admin_tool_compose_project.compose_file_manager.yml['services']['rqdash'] = {
                "image": f"ghcr.io/rtcamp/frappe-manager-rqdash:{self.version.version_string()}",
                "expose": ['9181'],
                "envrionment": {
                    "RQ_DASHBOARD_REDIS_URL": f"redis://{get_container_name_prefix(bench.name)}__redis-queue:6379"
                },
                "networks": {"site_network": None},
            }

            admin_tool_compose_project.compose_file_manager.set_service_command("rqdash", "--url-prefix /rqdash")

            admin_tools_image_info = admin_tool_compose_project.compose_file_manager.get_all_images()

            for image in [admin_tools_image_info["rqdash"]]:
                pull_image = f"{image['name']}:{image['tag']}"

                if pull_image not in self.pulled_images_list:
                    richprint.change_head(f"Pulling Image {pull_image}")
                    output = DockerClient().pull(container_name=pull_image, stream=True)
                    richprint.live_lines(output, padding=(0, 0, 0, 2))
                    richprint.print(f"Image pulled [blue]{pull_image}[/blue]")
                    self.pulled_images_list.append(pull_image)

            admin_tool_compose_project.compose_file_manager.set_root_networks_name(
                "site-network", get_container_name_prefix(bench.name), external=True
            )
            admin_tool_compose_project.compose_file_manager.set_all_images(admin_tools_image_info)
            admin_tool_compose_project.compose_file_manager.set_container_names(get_container_name_prefix(bench.name))
            admin_tool_compose_project.compose_file_manager.set_version(str(self.version))
            admin_tool_compose_project.compose_file_manager.write_to_file()

            richprint.print(f"Migrated [blue]{bench.name}[/blue] admin-tools compose file.")
