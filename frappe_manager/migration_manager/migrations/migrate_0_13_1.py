from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import (
    MigrationServicesManager,
)
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.version import Version
from frappe_manager import CLI_DIR, CLI_SERVICES_DIRECTORY


class MigrationV0131(MigrationBase):
    version = Version("0.13.1")

    def __init__(self):
        super().init()
        self.benches_dir = CLI_DIR / "sites"
        self.services_path = CLI_SERVICES_DIRECTORY

    def up(self):
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

        self.services_manager: MigrationServicesManager = MigrationServicesManager()

        if not self.services_manager.compose_project.compose_file_manager.exists():
            raise MigrationExceptionInBench(
                f"Services compose at {self.services_manager.compose_project.compose_file_manager} not found."
            )

        richprint.change_head("Adding fm header config to nginx-proxy")
        # create file fmheaders.conf
        fm_headers_conf_path = self.services_path / 'nginx-proxy' / 'confd' / 'fm_headers.conf'
        add_header = f'add_header X-Powered-By "Frappe-Manager {self.version.version_string()}";'

        fm_headers_conf_path.write_text(add_header)

        if self.services_manager.compose_project.is_service_running('global-nginx-proxy'):
            self.services_manager.compose_project.docker.compose.up(services=['global-nginx-proxy'], stream=False)
            self.services_manager.compose_project.docker.compose.restart(services=['global-nginx-proxy'], stream=False)

        richprint.print("Added fm header config to nginx-proxy.")

        richprint.print(f"Successfull", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

    def down(self):
        # richprint.print(f"Started",prefix=f"[ Migration v{str(self.version)} ][ROLLBACK] : ")
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup, force=True)

        richprint.print(f"Successfull", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)
