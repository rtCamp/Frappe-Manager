from abc import ABC
from logging import Logger
from pathlib import Path
import json
from frappe_manager import CLI_DIR
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench, BackupFailureException
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.migration_manager.version import Version
from frappe_manager.migration_manager.bench_migration_state import get_bench_migration_version
from frappe_manager.logger import log
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo, MariaDBManager
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.utils.helpers import capture_and_format_exception


# @runtime_checkable
class MigrationBase(ABC):
    version: Version = Version("0.0.0")
    benches_dir: Path = CLI_DIR / "sites"
    skip: bool = False
    migration_executor = None
    logger: Logger = log.get_logger()
    
    def __init__(self, output_handler: OutputHandler | None = None):
        self.output = output_handler or RichOutputHandler()

    def init(self):
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager(services_path=CLI_DIR / 'services')

    def set_migration_executor(self, migration_executor):
        self.migration_executor = migration_executor
        if hasattr(migration_executor, 'output'):
            self.output = migration_executor.output

    def get_rollback_version(self):
        return self.version

    def up(self):
        if self.skip:
            return True

        self.output.print("", emoji_code="")
        self.output.print("=" * 60, emoji_code="")
        self.output.print(f':package: [bold][blue]v{str(self.version)}[/blue][/bold]', emoji_code="")
        self.output.print("=" * 60, emoji_code="")
        self.logger.info(f"v{str(self.version)}: Started")
        self.logger.info("-" * 40)

        self.init()
        
        if self.migration_executor and self.migration_executor.system_needs_migration:
            self.services_basic_backup()
            self.migrate_services()
        
        self.migrate_benches()

        self.logger.info("-" * 40)

    def down(self):
        self.output.change_head(f"Working on v{str(self.version)} rollback")
        self.logger.info("-" * 40)

        # undo each bench
        for bench_name, bench_data in self.migration_executor.migrate_benches.items():
            if not bench_data['exception']:
                self.undo_bench_migrate(bench_data['object'])

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup, force=True)
            # self.output.print(f'Restored {backup.bench}'s {backup.src.name}.')

        self.undo_services_migrate()

        self.output.print(f"[bold]v{str(self.version)}[/bold] rollback successfull")
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

        all_benches = self.benches_manager.get_all_benches()

        # migrate each bench
        for bench_name, bench_path in all_benches.items():
            if self.migration_executor.target_benches and bench_name not in self.migration_executor.target_benches:
                continue
            
            if bench_name in self.migration_executor.exclude_benches:
                self.output.print(f"Skipping {bench_name} (--exclude-bench)", emoji_code="")
                continue

            bench = MigrationBench(name=bench_name, path=bench_path.parent)
            
            bench_version = get_bench_migration_version(bench.path)
            
            # Check if bench is already at or above this migration version
            if bench_version >= self.version:
                self.output.print(f"Bench {bench_name} already at v{bench_version}, skipping migration to v{self.version}")
                continue

            if bench.name in self.migration_executor.migrate_benches.keys():
                bench_info = self.migration_executor.migrate_benches[bench.name]
                if bench_info['exception']:
                    self.output.print(f"Skipping migration for failed bench [blue]{bench.name}[/blue]")
                    main_error = True
                    continue

            self.migration_executor.set_bench_data(bench, migration_version=self.version)
            try:
                self.bench_basic_backup(bench)
                self.migrate_bench(bench)
            except Exception as e:
                traceback_str = capture_and_format_exception()
                self.logger.error(f"{bench.name} [ EXCEPTION TRACEBACK ]:\n {traceback_str}")
                self.output.update_live()
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
        self.output.print(f"Migrating bench [bold][blue]{bench.name}[/blue][/bold]")

        if self.migration_executor.skip_backup:
            self.output.warning(f"Skipping backup for {bench.name} (--skip-backup)")
            return

        if bench.name in self.migration_executor.skip_backup_for:
            self.output.warning(f"Skipping backup for {bench.name} (--skip-backup-for)")
            return

        bench_config_path = bench.path / "bench_config.toml"
        if bench_config_path.exists():
            self.backup_manager.backup(bench_config_path, bench_name=bench.name)

        self.backup_manager.backup(bench.path / "docker-compose.yml", bench_name=bench.name)

        bench_common_site_config = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        self.backup_manager.backup(bench_common_site_config, bench_name=bench.name)

        bench_site_config = bench.path / "workspace" / "frappe-bench" / "sites" / bench.name / "site_config.json"
        self.backup_manager.backup(bench_site_config, bench_name=bench.name)

        bench_db_info = DatabaseServerServiceInfo.import_from_bench(bench_path=bench.path,bench_name=bench.name,raise_exception=False)

        self.bench_db_backup(
            bench=bench,
            db_info=bench_db_info,
            compose_project=bench.compose_project,
            backup_manager=self.backup_manager,
        )

    def migrate_bench(self, bench: MigrationBench):
        pass

    def undo_bench_migrate(self, bench: MigrationBench):
        pass

    def _resolve_database_name(self, bench: MigrationBench, db_info: DatabaseServerServiceInfo) -> str | None:
        if db_info.name:
            return db_info.name
        
        bench_config_path = bench.path / "bench_config.toml"
        if bench_config_path.exists():
            try:
                import tomlkit
                with open(bench_config_path, 'r') as f:
                    bench_config = tomlkit.parse(f.read())
                    db_name = bench_config.get("db_name")
                    if db_name:
                        return db_name
            except Exception as e:
                self.output.warning(f"Failed to read db_name from bench_config.toml: {e}")
        
        return None

    def bench_db_backup(
        self,
        bench: MigrationBench,
        db_info: DatabaseServerServiceInfo,
        compose_project,
        backup_manager: BackupManager,
    ):
        self.output.change_head(f'Commencing db {bench.name} backup')

        db_name = self._resolve_database_name(bench, db_info)
        
        if not db_name:
            self.output.warning(
                f"Could not determine database name for {bench.name}.\n"
                f"Checked: site_config.json, db_info, bench_config.toml."
            )
            
            skip_backup_prompt = [
                "Database backup will be skipped for this bench.",
                "Do you want to continue migration without database backup?"
            ]
            
            user_choice = self.output.prompt_ask(
                prompt="\n".join(skip_backup_prompt),
                choices=["yes", "no"]
            )
            
            if user_choice == "no":
                self.output.display_error(f"User chose to abort migration for {bench.name}")
                raise MigrationExceptionInBench(
                    f"Migration aborted for {bench.name}: Unable to determine database name. "
                    f"User declined to skip database backup."
                )
            
            self.output.warning(f"Skipping database backup for {bench.name}")
            return

        mariadb_manager = MariaDBManager(
            db_info,
            compose_project.compose_file_manager,
            compose_project.docker,
            run_on_compose_service="frappe"
        )

        from datetime import datetime

        current_datetime = datetime.now()
        formatted_date = current_datetime.strftime("%d-%m-%Y--%H-%M-%S")

        host_backup_dir: Path = bench.path / "workspace" / ".cache"
        db_sql_file_name = f"db-{bench.name}-{formatted_date}.sql"

        host_db_sql_file_path: Path = host_backup_dir / db_sql_file_name
        container_db_sql_file_path: Path = Path("/workspace") / ".cache" / db_sql_file_name

        backup_gz_file_backup_data_path: Path = (
            bench.path / backup_manager.bench_backup_dir / self.version.version / f'{db_sql_file_name}.gz'
        )

        mariadb_manager.db_export(db_name, container_db_sql_file_path)

        import gzip
        import shutil

        # Compress the file using gzip
        with open(host_db_sql_file_path, 'rb') as f_in:
            with gzip.open(backup_gz_file_backup_data_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        host_db_sql_file_path.unlink()

        self.output.print(f'[blue]{bench.name}[/blue] db backup completed successfully.')
