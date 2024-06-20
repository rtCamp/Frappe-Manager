from pathlib import Path
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.version import Version
from frappe_manager.utils.helpers import get_container_name_prefix
from frappe_manager.migration_manager.backup_manager import BackupManager


class MigrationV0120(MigrationBase):
    version = Version("0.12.0")

    def init(self):
        self.cli_dir: Path = Path.home() / 'frappe'
        self.benches_dir = self.cli_dir / "sites"
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager(
            services_path=self.cli_dir / 'services'
        )

    def migrate_services(self):
        # Pulling latest image
        self.image_info = {"tag": self.version.version_string(), "name": "ghcr.io/rtcamp/frappe-manager-frappe"}
        pull_image = f"{self.image_info['name']}:{self.image_info['tag']}"

        richprint.change_head(f"Pulling Image {pull_image}")
        output = DockerClient().pull(container_name=pull_image, stream=True)
        richprint.live_lines(output, padding=(0, 0, 0, 2))
        richprint.print(f"Image pulled [blue]{pull_image}[/blue]")

    def migrate_bench(self, bench: MigrationBench):
        bench.compose_project.down_service(volumes=False)
        self.migrate_bench_compose(bench)
        self.migrate_workers_compose(bench)

    def migrate_bench_compose(self, bench: MigrationBench):
        richprint.change_head("Migrating bench compose")

        if not bench.compose_project.compose_file_manager.exists():
            richprint.print(f"Failed to migrate {bench.name} compose file.")
            raise MigrationExceptionInBench(f"{bench.compose_project.compose_file_manager.compose_path} not found.")

        images_info = bench.compose_project.compose_file_manager.get_all_images()

        # for all services
        images_info["frappe"] = self.image_info
        images_info["socketio"] = self.image_info
        images_info["schedule"] = self.image_info

        compose_yml = bench.compose_project.compose_file_manager.yml
        # remove restart: from all the services
        for service in compose_yml["services"]:
            try:
                del compose_yml["services"][service]["restart"]
            except KeyError as e:
                self.logger.error(
                    f"{bench.name} worker not able to delete 'restart: always' attribute from compose file.{e}"
                )
                pass

        richprint.print("Removed 'restart: always'.")

        bench.compose_project.compose_file_manager.set_version(str(self.version))
        bench.compose_project.compose_file_manager.set_all_images(images_info)
        bench.compose_project.compose_file_manager.write_to_file()

        richprint.print(f"Migrated {bench.name} compose file.")

    def migrate_workers_compose(self, bench: MigrationBench):
        if bench.workers_compose_project.compose_file_manager.compose_path.exists():
            richprint.print("Migrating workers compose")

            # workers image set
            workers_info = bench.workers_compose_project.compose_file_manager.get_all_images()

            for worker in workers_info.keys():
                workers_info[worker] = self.image_info

            worker_compose_yml = bench.workers_compose_project.compose_file_manager.yml
            for service in worker_compose_yml["services"]:
                try:
                    del worker_compose_yml["services"][service]["restart"]
                except KeyError as e:
                    self.logger.error(
                        f"{bench.name} worker not able to delete 'restart: always' attribute from compose file.{e}"
                    )
                    pass

            bench.workers_compose_project.compose_file_manager.set_top_networks_name(
                "site-network", get_container_name_prefix(bench.name)
            )
            bench.workers_compose_project.compose_file_manager.set_all_images(workers_info)

            bench.workers_compose_project.compose_file_manager.set_container_names(
                get_container_name_prefix(bench.name)
            )
            bench.workers_compose_project.compose_file_manager.write_to_file()

        richprint.print(f"Migrated [blue]{bench.name}[/blue] compose file.")
