from pathlib import Path
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.version import Version
from frappe_manager.migration_manager.backup_manager import BackupManager


class MigrationV0131(MigrationBase):
    version = Version("0.13.1")

    def init(self):
        self.cli_dir: Path = Path.home() / 'frappe'
        self.benches_dir = self.cli_dir / "sites"
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager(
            services_path=self.cli_dir / 'services'
        )

    def migrate_services(self):
        if not self.services_manager.compose_project.compose_file_manager.exists():
            raise MigrationExceptionInBench(
                f"Services compose at {self.services_manager.compose_project.compose_file_manager} not found."
            )

        richprint.change_head("Adding fm header config to nginx-proxy")

        # create file fmheaders.conf
        fm_headers_conf_path = self.services_manager.services_path / 'nginx-proxy' / 'confd' / 'fm_headers.conf'
        add_header = f'add_header X-Powered-By "Frappe-Manager {self.version.version_string()}";'

        fm_headers_conf_path.write_text(add_header)

        if self.services_manager.compose_project.is_service_running('global-nginx-proxy'):
            self.services_manager.compose_project.docker.compose.up(services=['global-nginx-proxy'], stream=False)
            self.services_manager.compose_project.docker.compose.restart(services=['global-nginx-proxy'], stream=False)

        richprint.print("Added fm header config to nginx-proxy.")
