from abc import ABC
from logging import Logger
from pathlib import Path
from frappe_manager import CLI_DIR
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.migration_manager.version import Version
from frappe_manager.logger import log
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo, MariaDBManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import capture_and_format_exception


# @runtime_checkable
class MigrationBase(ABC):
    version: Version = Version("0.0.0")
    benches_dir: Path = CLI_DIR / "sites"
    skip: bool = False
    migration_executor = None
    logger: Logger = log.get_logger()

    def init(self):
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager(services_path=CLI_DIR / 'services')

    def set_migration_executor(self, migration_executor):
        self.migration_executor = migration_executor

    def get_rollback_version(self):
        return self.version

    def up(self):
        if self.skip:
            return True

        richprint.stdout.rule(f':package: [bold][blue]v{str(self.version)}[/blue][bold]')
        self.logger.info(f"v{str(self.version)}: Started")
        self.logger.info("-" * 40)

        self.init()
        self.services_basic_backup()
        self.migrate_services()
        self.migrate_benches()

        self.logger.info("-" * 40)

    def down(self):
        richprint.change_head(f"Working on v{str(self.version)} rollback.")
        self.logger.info("-" * 40)

        # undo each bench
        for bench_name, bench_data in self.migration_executor.migrate_benches.items():
            if not bench_data['exception']:
                self.undo_bench_migrate(bench_data['object'])

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup, force=True)
            # richprint.print(f'Restored {backup.bench}'s {backup.src.name}.')

        self.undo_services_migrate()

        richprint.print(f"[bold]v{str(self.version)}[/bold] rollback successfull.")
        self.logger.info("-" * 40)

    def services_basic_backup(self):
        if not self.services_manager.compose_project.compose_file_manager.exists():
            raise MigrationExceptionInBench(
                f"Services compose at {self.services_manager.compose_project.compose_file_manager} not found."
            )
        self.backup_manager.backup(self.services_manager.compose_project.compose_file_manager.compose_path)

    def migrate_services(self):
        pass

    def undo_services_migrate(self):
        pass

    def migrate_benches(self):
        main_error = False

        # migrate each bench
        for bench_name, bench_path in self.benches_manager.get_all_benches().items():
            bench = MigrationBench(name=bench_name, path=bench_path.parent)

            if bench.name in self.migration_executor.migrate_benches.keys():
                bench_info = self.migration_executor.migrate_benches[bench.name]
                if bench_info['exception']:
                    richprint.print(f"Skipping migration for failed bench [blue]{bench.name}[/blue].")
                    main_error = True
                    continue

            self.migration_executor.set_bench_data(bench, migration_version=self.version)
            try:
                self.bench_basic_backup(bench)
                self.migrate_bench(bench)
            except Exception as e:
                traceback_str = capture_and_format_exception()
                self.logger.error(f"{bench.name} [ EXCEPTION TRACEBACK ]:\n {traceback_str}")
                richprint.update_live()
                main_error = True
                self.migration_executor.set_bench_data(bench, e, self.version)

                # restore all backup files
                for backup in self.backup_manager.backups:
                    if backup.bench == bench.name:
                        self.backup_manager.restore(backup, force=True)

                self.undo_bench_migrate(bench)
                self.logger.info(f'Undo successfull for bench: {bench.name}')
                bench.compose_project.down_service(volumes=False, timeout=5)

        if main_error:
            raise MigrationExceptionInBench('')

    def bench_basic_backup(self, bench: MigrationBench):
        richprint.print(f"Migrating bench [bold][blue]{bench.name}[/blue][/bold]")

        # backup docker compose.yml
        self.backup_manager.backup(bench.path / "docker-compose.yml", bench_name=bench.name)

        # backup common_site_config.json
        bench_common_site_config = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        self.backup_manager.backup(bench_common_site_config, bench_name=bench.name)

        # backup site_config.json
        bench_site_config = bench.path / "workspace" / "frappe-bench" / "sites" / bench.name / "site_config.json"
        self.backup_manager.backup(bench_site_config, bench_name=bench.name)

        server_db_info: DatabaseServerServiceInfo = DatabaseServerServiceInfo.import_from_compose_file(
            'global-db', self.services_manager.compose_project
        )

        self.bench_db_backup(
            bench=bench,
            server_db_info=server_db_info,
            services_manager=self.services_manager,
            backup_manager=self.backup_manager,
        )

    def migrate_bench(self, bench: MigrationBench):
        pass

    def undo_bench_migrate(self, bench: MigrationBench):
        pass

    def bench_db_backup(
        self,
        bench: MigrationBench,
        server_db_info: DatabaseServerServiceInfo,
        services_manager: MigrationServicesManager,
        backup_manager: BackupManager,
    ):
        bench_db_info = bench.get_db_connection_info()
        bench_db_name = bench_db_info["name"]

        richprint.change_head(f'Commencing db {bench.name} backup')

        mariadb_manager = MariaDBManager(server_db_info, services_manager.compose_project)

        from datetime import datetime

        current_datetime = datetime.now()
        formatted_date = current_datetime.strftime("%d-%m-%Y--%H-%M-%S")

        container_backup_dir: Path = Path("/var/log/mysql")
        host_backup_dir: Path = services_manager.services_path / 'mariadb' / 'logs'
        db_sql_file_name = f"db-{bench.name}-{formatted_date}.sql"

        host_db_sql_file_path: Path = host_backup_dir / db_sql_file_name
        container_db_sql_file_path: Path = container_backup_dir / db_sql_file_name

        backup_gz_file_backup_data_path: Path = (
            bench.path / backup_manager.bench_backup_dir / self.version.version / f'{db_sql_file_name}.gz'
        )

        mariadb_manager.db_export(bench_db_name, container_db_sql_file_path)

        import gzip
        import shutil

        # Compress the file using gzip
        with open(host_db_sql_file_path, 'rb') as f_in:
            with gzip.open(backup_gz_file_backup_data_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        host_db_sql_file_path.unlink()

        richprint.print(f'[blue]{bench.name}[/blue] db backup completed successfully.')
