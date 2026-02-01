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
from frappe_manager.migration_manager.migration_constants import MINIMUM_SUPPORTED_VERSION
from frappe_manager.migration_manager.migration_validator import MigrationValidator, BenchFilter
from frappe_manager.migration_manager.migration_discovery import MigrationDiscovery
from frappe_manager.migration_manager.migration_orchestrator import MigrationOrchestrator
from frappe_manager.migration_manager.migration_error_handler import MigrationErrorHandler
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler


def needs_migration(fm_config_manager: FMConfigManager) -> bool:
    prev_version = fm_config_manager.version
    current_version = Version(get_current_fm_version())
    return prev_version < current_version


def needs_fm_infrastructure_migration(fm_config_manager: FMConfigManager) -> bool:
    current_version = Version(get_current_fm_version())
    fm_infrastructure_version = fm_config_manager.get_system_migration_version()
    return fm_infrastructure_version < current_version


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
        skip_backup_for: list[str] | None = None,
        exclude_benches: list[str] | None = None,
        force: bool = False,
        target_benches: list[str] | None = None,
        migrate_fm_infrastructure: bool = False,
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
        self.skip_backup_for = skip_backup_for or []
        self.exclude_benches = exclude_benches or []
        self.force = force
        self.target_benches = target_benches
        self.migrate_fm_infrastructure = migrate_fm_infrastructure
        self.fm_infrastructure_needs_migration = False
        self.output = output_handler or RichOutputHandler()

        # Initialize helper classes (composition)
        bench_filter = BenchFilter(target_benches=target_benches, exclude_benches=self.exclude_benches)
        self.validator = MigrationValidator(
            prev_version=self.prev_version,
            current_version=self.current_version,
            bench_filter=bench_filter,
            output_handler=self.output,
        )
        self.discovery = MigrationDiscovery(self.migrations_path, output_handler=self.output)
        self.orchestrator = MigrationOrchestrator(self)
        self.error_handler = MigrationErrorHandler(self)

    def _get_minimum_bench_version(self) -> Version:
        """Get the minimum migration version across all target benches.

        Returns the lowest version that needs migration. This is used to determine
        which migration classes need to be loaded.

        DEPRECATED: Use validator.get_minimum_bench_version() instead.
        """
        return self.validator.get_minimum_bench_version()

    def _check_benches_need_migration(self) -> bool:
        """Check if any benches need migration to current version.

        DEPRECATED: Use validator.check_benches_need_migration() instead.
        """
        return self.validator.check_benches_need_migration()

    def execute(self):
        """
        Execute the migration.
        This method will execute the migration and return the number of
        executed statements.
        """

        fm_infrastructure_version_outdated = self.prev_version < self.current_version
        fm_infrastructure_needs_migration = self.migrate_fm_infrastructure and fm_infrastructure_version_outdated
        self.fm_infrastructure_needs_migration = fm_infrastructure_needs_migration
        benches_need_migration = self._check_benches_need_migration()

        if not fm_infrastructure_needs_migration and not benches_need_migration:
            return True

        effective_prev_version = self.prev_version
        if benches_need_migration:
            min_bench_version = self._get_minimum_bench_version()
            effective_prev_version = min(self.prev_version, min_bench_version)

        if effective_prev_version != Version("0.0.0") and effective_prev_version < MINIMUM_SUPPORTED_VERSION:
            self.validator.validate_version_support(effective_prev_version)
            return False

        # Discovery: Load migration classes dynamically
        self.migrations = self.discovery.discover_migrations(effective_prev_version, self.current_version, self)

        if self.migrations:
            if fm_infrastructure_needs_migration:
                self.output.print(
                    f"FM Infrastructure: [yellow]v{self.prev_version}[/yellow] → [green]v{self.current_version}[/green]",
                    emoji_code="",
                )
                self.output.print("  • CLI configuration", emoji_code="")
                self.output.print("  • Global services (MariaDB, Nginx-Proxy)", emoji_code="")

            if benches_need_migration and self.target_benches:
                self.output.print("", emoji_code="")
                self.output.print("Benches:", emoji_code="")
                benches_manager = MigrationBenches(CLI_BENCHES_DIRECTORY)
                all_benches = benches_manager.get_all_benches()

                for bench_name in self.target_benches:
                    if bench_name in self.exclude_benches:
                        continue

                    if bench_name in all_benches:
                        bench_path = all_benches[bench_name].parent
                        bench_version = get_bench_migration_version(bench_path)

                        if bench_version < self.current_version:
                            self.output.print(
                                f"  • {bench_name}: [yellow]v{bench_version}[/yellow] → [green]v{self.current_version}[/green]",
                                emoji_code="",
                            )

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

            if self.target_benches:
                benches_manager = MigrationBenches(CLI_BENCHES_DIRECTORY)
                all_benches = benches_manager.get_all_benches()
                running = []
                for bench_name in self.target_benches:
                    if bench_name in all_benches:
                        bench_path = all_benches[bench_name].parent
                        bench = MigrationBench(bench_name, bench_path)
                        if bench.running or bench.workers_running:
                            running.append(bench_name)
                if running:
                    self.output.warning(
                        f"The following target benches are currently running and will be restarted (containers recreated) during migration: {', '.join(running)}"
                    )
                    self.output.print(
                        "If you'd prefer no disruption, stop these benches (fm stop <bench>) and re-run migration."
                    )
                    self.output.print("", emoji_code="")

            if not self.force:
                continue_migration = self.output.prompt_ask(
                    prompt="Do you want to proceed?",
                    choices=[
                        {"name": "yes - Start migration", "value": "yes"},
                        {"name": "no - Abort and revert to previous fm version", "value": "no"},
                    ],
                )
            else:
                continue_migration = "yes"
                self.output.print("Proceeding with migration (--force)", emoji_code="")

            if continue_migration == "no":
                self.output.print("", emoji_code="")
                self.output.print(
                    f"Migration aborted. To revert to v{str(self.prev_version.version)}, run:", emoji_code=""
                )
                self.output.print(f"  uv tool install frappe-manager=={str(self.prev_version.version)}", emoji_code="")
                self.output.print("", emoji_code="")
                return False

        # Orchestration: Execute migrations with error handling
        try:
            self.orchestrator.execute_migrations()
            self.undo_stack = self.orchestrator.undo_stack
            self.error_handler.finalize_success()
            return True

        except MigrationExceptionInBench as e:
            return self.error_handler.handle_bench_migration_failure(e)

        except Exception as e:
            return self.error_handler.handle_system_migration_failure(e)

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
        """Get migration data for a specific bench."""
        try:
            data = self.migrate_benches[bench_name]
        except KeyError as e:
            return None
        return data

    def rollback(self):
        """
        Rollback the migration.

        DEPRECATED: Use orchestrator.rollback_migrations() instead.
        This method is kept for backward compatibility.
        """
        self.orchestrator.undo_stack = self.undo_stack
        self.orchestrator.rollback_migrations()
