from pathlib import Path
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.version import Version
from frappe_manager.migration_manager.backup_manager import BackupManager


class MigrationV0110(MigrationBase):
    version = Version("0.11.0")

    def init(self):
        self.cli_dir: Path = Path.home() / 'frappe'
        self.benches_dir = self.cli_dir / "sites"
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager(
            services_path=self.cli_dir / 'services'
        )

    def migrate_bench(self, bench: MigrationBench):
        bench.compose_project.down_service(volumes=False)
        self.migrate_bench_compose(bench)

    def migrate_bench_compose(self, bench: MigrationBench):
        richprint.change_head("Migrating bench compose")

        if not bench.compose_project.compose_file_manager.exists():
            richprint.print(f"Failed to migrate {bench.name} compose file.")
            raise MigrationExceptionInBench(f"{bench.compose_project.compose_file_manager.compose_path} not found.")

        # change image tag to the latest
        # in this migration only tag of frappe container is changed
        images_info = bench.compose_project.compose_file_manager.get_all_images()
        image_info = images_info['frappe']

        # get v0.11.0 frappe image
        image_info['tag'] = self.version.version_string()
        image_info['name'] = 'ghcr.io/rtcamp/frappe-manager-frappe'

        output = bench.compose_project.docker.pull(
            container_name=f"{image_info['name']}:{image_info['tag']}", stream=True
        )
        richprint.live_lines(output, padding=(0, 0, 0, 2))

        # Use migrate_images to update frappe image and version atomically
        tag_updates = {'frappe': image_info['tag']}
        bench.compose_project.compose_file_manager.migrate_images(
            tag_updates=tag_updates,
            new_version=str(self.version)
        )

        richprint.print(f"Migrated [blue]{bench.name}[/blue] compose file.")
