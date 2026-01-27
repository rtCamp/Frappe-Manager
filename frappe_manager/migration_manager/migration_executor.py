import shutil
from typing import Optional
from frappe_manager.migration_manager.migration_helpers import MigrationBench, MigrationBenches
import importlib
import pkgutil
from pathlib import Path
from frappe_manager import CLI_DIR, CLI_SITES_ARCHIVE, CLI_BENCHES_DIRECTORY
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.migration_exections import (
    MigrationExceptionInBench,
)
from frappe_manager.utils.helpers import capture_and_format_exception, get_current_fm_version
from frappe_manager.logger import log
from frappe_manager.migration_manager.version import Version
from frappe_manager.migration_manager.bench_migration_state import get_bench_migration_version
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler


MINIMUM_SUPPORTED_VERSION = Version("0.18.0")


def needs_migration(fm_config_manager: FMConfigManager) -> bool:
    prev_version = fm_config_manager.version
    current_version = Version(get_current_fm_version())
    return prev_version < current_version


def needs_system_migration(fm_config_manager: FMConfigManager) -> bool:
    current_version = Version(get_current_fm_version())
    system_version = fm_config_manager.get_system_migration_version()
    return system_version < current_version


def get_benches_needing_migration(benches_directory: Path, current_version: Version) -> list[str]:
    from frappe_manager.migration_manager.bench_migration_state import bench_needs_migration
    from frappe_manager import CLI_BENCH_CONFIG_FILE_NAME
    
    needs_migration_list = []
    
    if not benches_directory.exists():
        return needs_migration_list
    
    for bench_path in benches_directory.iterdir():
        if bench_path.is_dir():
            bench_config = bench_path / CLI_BENCH_CONFIG_FILE_NAME
            if bench_config.exists():
                if bench_needs_migration(bench_path, current_version):
                    needs_migration_list.append(bench_path.name)
    
    return needs_migration_list


class MigrationExecutor:
    """
    Migration executor class.

    This class is responsible for executing migrations.
    """

    def __init__(
        self,
        fm_config_manager: FMConfigManager,
        skip_backup: bool = False,
        skip_backup_for: list[str] = [],
        exclude_benches: list[str] = [],
        force: bool = False,
        target_benches: list[str] | None = None,
        migrate_system: bool = False,
        output_handler: OutputHandler | None = None,
    ):
        self.fm_config_manager: FMConfigManager = fm_config_manager
        self.prev_version = self.fm_config_manager.version
        self.rollback_version = self.fm_config_manager.version
        self.current_version = Version(get_current_fm_version())
        self.migrations_path = Path(__file__).parent / "migrations"
        self.logger = log.get_logger()
        self.migrations = []
        self.undo_stack = []
        self.migrate_benches = {}
        self.skip_backup = skip_backup
        self.skip_backup_for = skip_backup_for
        self.exclude_benches = exclude_benches
        self.force = force
        self.target_benches = target_benches
        self.migrate_system = migrate_system
        self.system_needs_migration = False
        self.output = output_handler or RichOutputHandler()

    def _get_minimum_bench_version(self) -> Version:
        """Get the minimum migration version across all target benches.
        
        Returns the lowest version that needs migration. This is used to determine
        which migration classes need to be loaded.
        """
        if not self.target_benches:
            return self.current_version
        
        benches_manager = MigrationBenches(CLI_BENCHES_DIRECTORY)
        all_benches = benches_manager.get_all_benches()
        min_version = self.current_version
        
        for bench_name, bench_path in all_benches.items():
            if bench_name not in self.target_benches:
                continue
            
            if bench_name in self.exclude_benches:
                continue
            
            bench_version = get_bench_migration_version(bench_path.parent)
            
            # If bench has lower version than current minimum, update it
            # This includes 0.0.0 versions (benches without migration_state)
            if bench_version < min_version:
                min_version = bench_version
        
        return min_version

    def _check_benches_need_migration(self) -> bool:
        """Check if any benches need migration to current version."""
        if not self.target_benches:
            return False
        
        benches_manager = MigrationBenches(CLI_BENCHES_DIRECTORY)
        all_benches = benches_manager.get_all_benches()
        
        for bench_name, bench_path in all_benches.items():
            if bench_name not in self.target_benches:
                continue
            
            if bench_name in self.exclude_benches:
                continue
            
            bench_version = get_bench_migration_version(bench_path.parent)
            if bench_version < self.current_version:
                return True
        
        return False

    def execute(self):
        """
        Execute the migration.
        This method will execute the migration and return the number of
        executed statements.
        """

        # System needs migration if user requested it AND versions differ
        system_version_outdated = self.prev_version < self.current_version
        system_needs_migration = self.migrate_system and system_version_outdated
        self.system_needs_migration = system_needs_migration  # Store for migration classes
        benches_need_migration = self._check_benches_need_migration()

        if not system_needs_migration and not benches_need_migration:
            return True

        # Determine effective prev_version for migration loading
        # When benches need migration, use minimum bench version to ensure migrations load
        effective_prev_version = self.prev_version
        if benches_need_migration:
            min_bench_version = self._get_minimum_bench_version()
            # Use the lower of system version or minimum bench version
            effective_prev_version = min(self.prev_version, min_bench_version)

        # Skip minimum version check if effective version is 0.0.0
        # (means bench has no migration_state, not that it's genuinely old)
        if effective_prev_version != Version("0.0.0") and effective_prev_version < MINIMUM_SUPPORTED_VERSION:
            self.output.display_error(
                f"Cannot migrate from v{effective_prev_version.version}. "
                f"Minimum supported version is v{MINIMUM_SUPPORTED_VERSION.version}."
            )
            self.output.display_error(
                f"\nPlease upgrade to v{MINIMUM_SUPPORTED_VERSION.version} first, then upgrade to v{self.current_version.version}."
            )
            self.output.display_error(
                f"\nMigration path: v{effective_prev_version.version} → v{MINIMUM_SUPPORTED_VERSION.version} → v{self.current_version.version}"
            )
            return False

        current_migration = None

        # Dynamically import all modules in the 'migrations' subfolder
        for _, name, _ in pkgutil.iter_modules([str(self.migrations_path)]):
            try:
                module = importlib.import_module(f".migrations.{name}", __package__)
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and hasattr(attr, "up")
                        and hasattr(attr, "down")
                        and hasattr(attr, "set_migration_executor")
                        and hasattr(attr, "version")
                    ):
                        if not getattr(attr, "version") == Version('0.0.0'):
                            migration = attr(output_handler=self.output)
                            migration.set_migration_executor(migration_executor=self)
                            current_migration = migration

                            if migration.version > effective_prev_version and migration.version <= self.current_version:
                                self.migrations.append(migration)

            except Exception as e:
                exception_str = capture_and_format_exception()
                print(f"Failed to register migration {name}: {exception_str}")

        self.migrations = sorted(self.migrations, key=lambda x: x.version)

        if self.migrations:
            # Show what will be migrated
            if system_needs_migration:
                self.output.print(f"System: [yellow]v{self.prev_version}[/yellow] → [green]v{self.current_version}[/green]", emoji_code="")
            
            if benches_need_migration and self.target_benches:
                benches_manager = MigrationBenches(CLI_BENCHES_DIRECTORY)
                all_benches = benches_manager.get_all_benches()
                
                for bench_name in self.target_benches:
                    if bench_name in self.exclude_benches:
                        continue
                    
                    if bench_name in all_benches:
                        bench_path = all_benches[bench_name].parent
                        bench_version = get_bench_migration_version(bench_path)
                        
                        if bench_version < self.current_version:
                            self.output.print(f"{bench_name}: [yellow]v{bench_version}[/yellow] → [green]v{self.current_version}[/green]", emoji_code="")
            
            self.output.print("", emoji_code="")
            
            self.output.print("Migration versions:", emoji_code="")
            for migration in self.migrations:
                self.output.print(f"  • v{migration.version}", emoji_code="")
            
            self.output.print("", emoji_code="")
            self.output.print("This process may take a while.", emoji_code="")
            self.output.print(
                "Manual guide: https://github.com/rtCamp/Frappe-Manager/wiki/Migrations#manual-migration-procedure",
                emoji_code="",
            )

            self.output.print("", emoji_code="")
            
            if not self.force:
                continue_migration = self.output.prompt_ask(
                    prompt="Do you want to proceed?",
                    choices=[
                        {"name": "yes - Start migration", "value": "yes"},
                        {"name": "no - Abort and revert to previous fm version", "value": "no"}
                    ]
                )
            else:
                continue_migration = "yes"
                self.output.print("Proceeding with migration (--force)", emoji_code="")

            if continue_migration == "no":
                self.output.print("", emoji_code="")
                self.output.print(f"Migration aborted. To revert to v{str(self.prev_version.version)}, run:", emoji_code="")
                self.output.print(f"  uv tool install frappe-manager=={str(self.prev_version.version)}", emoji_code="")
                self.output.print("", emoji_code="")
                return False

        rollback = False
        archive = False

        # Ensure global services are running before starting migrations
        # Migrations may need docker networks created by global services
        self._ensure_global_services_running()

        exception_migration_in_bench_occured = False
        try:
            # run all the migrations
            prev_migration = None
            for migration in self.migrations:
                self.output.change_head(f"Running migration introduced in v{migration.version}")
                self.logger.info(f"[{migration.version}] : Migration starting")
                try:
                    self.undo_stack.append(migration)

                    migration.up()

                    prev_migration = migration
                    if not exception_migration_in_bench_occured:
                        self.rollback_version = prev_migration.get_rollback_version()

                except MigrationExceptionInBench as e:
                    captured_output = capture_and_format_exception(traceback_max_frames=0)
                    self.logger.error(f"[{migration.version}] : Migration Failed\n{captured_output}")

                    if migration.version < self.migrations[-1].version:
                        exception_migration_in_bench_occured = True
                        continue
                    raise e

                except Exception as e:
                    captured_output = capture_and_format_exception()
                    self.logger.error(f"[{migration.version}] : Migration Failed\n{captured_output}")
                    raise e

            if exception_migration_in_bench_occured:
                raise MigrationExceptionInBench('')

        except MigrationExceptionInBench as e:
            if self.migrate_benches:
                passed_print_head = True

                for bench, bench_status in self.migrate_benches.items():
                    if not bench_status["exception"]:
                        if passed_print_head:
                            self.output.print("", emoji_code="")
                            self.output.print("=" * 60, emoji_code="")
                            self.output.print("[bold green]Migration Passed Benches[/bold green]", emoji_code="")
                            self.output.print("=" * 60, emoji_code="")
                            passed_print_head = False

                        self.output.print(f"[green]Bench[/green]: {bench}", emoji_code="")

                failed_print_head = True

                for bench, bench_status in self.migrate_benches.items():
                    if bench_status["exception"]:
                        if failed_print_head:
                            self.output.print("", emoji_code="")
                            self.output.print("=" * 60, emoji_code="")
                            self.output.print("[bold red]Migration Failed Benches[/bold red]", emoji_code="")
                            self.output.print("=" * 60, emoji_code="")
                            failed_print_head = False

                        self.output.display_error(f"[red]Bench[/red]: {bench}", emoji_code="")

                        self.output.display_error(
                            f"[red]Failed Migration Version[/red]: {bench_status['last_migration_version']}",
                            emoji_code="",
                        )

                        self.output.display_error(
                            f"[red]Exception[/red]: {type(bench_status['exception']).__name__}",
                            emoji_code="",
                        )
                        self.output.print(f"   {bench_status['exception']}", emoji_code="")

                self.output.print(f"For error specifics, refer to {CLI_DIR}/logs/fm.log", emoji_code="")

                if not failed_print_head:
                    self.output.print("=" * 60, emoji_code="")
                else:
                    if not passed_print_head:
                        self.output.print("=" * 60, emoji_code="")

                archive_msg = [
                    'Available options after migrations failure :',
                    rf"[blue][yes][/blue] Archive failed benches : Benches that have failed will be rolled back to there last successfully completed migration version and stored in '{CLI_SITES_ARCHIVE}'.",
                    r'[blue][no][/blue] Revert migration : Restore the FM CLI and FM environment to the last successfully completed migration version for all benches.',
                    '\nDo you wish to archive all benches that failed during migration ?',
                ]
                
                if not self.force:
                    archive = self.output.prompt_ask(prompt="\n".join(archive_msg), choices=["yes", "no"])
                else:
                    archive = "no"
                    self.output.print("Rolling back all benches (--force)", emoji_code="")

                if archive == "no":
                    rollback = True

        except Exception as e:
            self.output.display_error(f"[red]Migration failed[red] : {e}", emoji_code="")
            rollback = True

        if archive == "yes":
            self.prev_version = self.undo_stack[-1].version
            for bench, bench_info in self.migrate_benches.items():
                if bench_info["exception"]:
                    archive_bench_path = CLI_SITES_ARCHIVE / bench
                    CLI_SITES_ARCHIVE.mkdir(exist_ok=True, parents=True)
                    shutil.move(bench_info["object"].path, archive_bench_path)
                    self.output.print(f"[bold]Archived bench :[/bold] [yellow]{bench}[/yellow]", emoji_code="")

        if rollback:
            self.rollback()
            self.fm_config_manager.version = self.rollback_version
            self.fm_config_manager.export_to_toml()
            self.output.print("", emoji_code="")
            self.output.print("Rollback complete.", emoji_code="")
            self.output.print("", emoji_code="")
            self.output.print(f"To revert FM CLI to v{str(self.rollback_version.version)}, run:", emoji_code="")
            self.output.print(f"  uv tool install frappe-manager=={str(self.rollback_version.version)}", emoji_code="")
            self.output.print("", emoji_code="")
            return False

        self.fm_config_manager.version = self.current_version
        self.fm_config_manager.export_to_toml()
        return True

    def set_bench_data(
        self,
        bench: MigrationBench,
        exception=None,
        migration_version: Optional[Version] = None,
        traceback_str: Optional[str] = None,
    ):
        self.migrate_benches[bench.name] = {
            "object": bench,
            "exception": exception,
            "last_migration_version": migration_version,
            "traceback": traceback_str,
        }

    def get_site_data(self, bench_name):
        try:
            data = self.migrate_benches[bench_name]
        except KeyError as e:
            return None
        return data

    def _ensure_global_services_running(self):
        from frappe_manager.services_manager.services import ServicesManager
        from frappe_manager.output_manager.silent_output import SilentOutputHandler
        from frappe_manager.output_manager.context_managers import temporary_stop
        
        try:
            services_manager = ServicesManager(output_handler=SilentOutputHandler())
            
            if not services_manager.path.exists():
                with temporary_stop(self.output):
                    self.output.print("Global services not initialized. Creating...", emoji_code=":construction:")
                    services_manager.init()
                    services_manager.entrypoint_checks(start=True)
                    self.output.print("Global services started successfully", emoji_code=":white_check_mark:")
                return
            
            services_manager.init()
            
            services_list = services_manager.compose_file_manager.get_services_list()
            all_running = all(services_manager.is_service_running(svc) for svc in services_list)
            
            if not all_running:
                with temporary_stop(self.output):
                    self.output.print("Global services not running. Starting them now...", emoji_code=":construction:")
                    services_manager.start_service()
                    self.output.print("Global services started successfully", emoji_code=":white_check_mark:")
        except Exception as e:
            self.logger.error(f"Failed to ensure global services are running: {e}")
            with temporary_stop(self.output):
                self.output.print(
                    "Warning: Could not verify/start global services. "
                    "Migration may fail if services are not running. "
                    "Try manually: fm services start",
                    emoji_code=":warning:"
                )

    def rollback(self):
        """
        Rollback the migration.
        This method will rollback the migration and return the number of
        rolled back statements.
        """

        # run all the migrations
        for migration in reversed(self.undo_stack):
            if migration.version > self.rollback_version:
                self.output.change_head(f"Rolling back migration introduced in v{migration.version}")
                self.logger.info(f"[{migration.version}] : Rollback starting")
                try:
                    migration.down()
                except Exception as e:
                    self.logger.error(f"[{migration.version}] : Rollback Failed\n{e}")
                    raise e
