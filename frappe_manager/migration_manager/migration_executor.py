from pathlib import Path

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.logger import get_logger
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.bench_migration_state import get_bench_migration_version
from frappe_manager.migration_manager.migration_constants import MINIMUM_SUPPORTED_VERSION
from frappe_manager.migration_manager.migration_discovery import MigrationDiscovery
from frappe_manager.migration_manager.migration_error_handler import MigrationErrorHandler
from frappe_manager.migration_manager.migration_exceptions import (
    MigrationExceptionInBench,
)
from frappe_manager.migration_manager.migration_helpers import MigrationBench, MigrationBenches
from frappe_manager.migration_manager.migration_orchestrator import MigrationOrchestrator
from frappe_manager.migration_manager.migration_validator import BenchFilter, MigrationValidator
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.utils.helpers import get_current_fm_version


class MigrationExecutor:
    """
    Migration executor class.

    This class is responsible for executing migrations.
    """

    def __init__(
        self,
        fm_config_manager: FMConfigManager,
        skip_backup: bool = False,
        skip_config_backup: bool = False,
        skip_db_backup: bool = False,
        exclude_benches: list[str] | None = None,
        auto_proceed: bool = False,
        rerun: bool = False,
        on_failure: str = "prompt",
        target_benches: list[str] | None = None,
        migrate_global_services: bool = False,
        output_handler: OutputHandler | None = None,
    ):
        self.fm_config_manager: FMConfigManager = fm_config_manager
        self.rerun = rerun
        # The services-tier ledger, via the one getter both migration gates use -- NEVER the
        # informational top-level `version` field. One source of truth: if the gate said
        # "behind", discovery here must agree, and vice versa.
        self.prev_version = self.fm_config_manager.get_system_migration_version()
        self.rollback_version = self.prev_version
        self.current_version = Version(get_current_fm_version())
        self.migrations_path = Path(__file__).parent / "migrations"
        self.logger = get_logger(component="migration")
        self.migrations = []
        self.undo_stack = []
        self.migrate_benches = {}
        self.skip_backup = skip_backup
        self.skip_config_backup = skip_config_backup
        self.skip_db_backup = skip_db_backup
        # No CLI flag feeds this anymore (backup skipping is by KIND now, not by bench), but
        # the frozen v0.19.0 migration reads it off the executor; it stays as an inert empty
        # list so upgrade chains keep working.
        self.skip_backup_for: list[str] = []
        self.exclude_benches = exclude_benches or []
        self.auto_proceed = auto_proceed
        self.on_failure = on_failure
        self.target_benches = target_benches
        self.migrate_global_services = migrate_global_services
        self.global_services_need_migration = False
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
        """Take the EXCLUSIVE host grip, run the migration, release the grip.

        The grip means "I am rewriting fm's state on this host": it is refused instantly
        while ANY other fm process runs (they all hold `locks/migration.lock` shared), and
        while held it keeps every other fm process out. Released in `finally`, explicitly,
        because the command that triggered an inline gate migration goes on to take its own
        SHARED grip afterwards -- and a process conflicts with its own holds.
        """
        from frappe_manager.utils import process_lock

        lock_path = process_lock.migration_lock_path()
        host_lock = process_lock.acquire(lock_path, exclusive=True, holder="migration")
        if host_lock is None:
            culprit = process_lock.read_holder(lock_path) or "another fm command"
            self.output.display_error(
                f"fm is busy: {culprit} is running on this host. Let it finish, then re-run."
            )
            return False
        try:
            return self._execute()
        finally:
            host_lock.close()

    def _execute(self):
        """The migration run itself; `execute` holds the host grip around it."""

        global_services_version_outdated = self.rerun or (self.prev_version < self.current_version)
        global_services_need_migration = self.migrate_global_services and global_services_version_outdated
        self.global_services_need_migration = global_services_need_migration
        benches_need_migration = self.rerun or self._check_benches_need_migration()

        if not global_services_need_migration and not benches_need_migration:
            return True

        effective_prev_version = self.prev_version
        if benches_need_migration:
            min_bench_version = self._get_minimum_bench_version()
            effective_prev_version = min(self.prev_version, min_bench_version)

        # When --rerun is active, ensure migrations are discovered even when
        # prev_version == current_version.  The strict ``<`` in discovery
        # (``from_version < migration.version``) would otherwise exclude the
        # current version's migration class.
        #
        # We narrow the range to the current base version's minor floor
        # (e.g. 0.18.9999 for 0.19.x) rather than ``0.0.0``, so that only
        # migrations belonging to the current release are included and old,
        # potentially non-idempotent migrations are not re-applied.
        if self.rerun and effective_prev_version >= self.current_version:
            from packaging.version import Version as PV

            parsed = PV(self.current_version.version)
            base = parsed.base_version  # e.g. "0.19.0"
            parts = base.split(".")
            floor = Version(f"{parts[0]}.{int(parts[1]) - 1}.9999")
            effective_prev_version = min(effective_prev_version, floor)

        # 0.0.0 (unknown) refuses exactly like below-minimum does: the validator prints the
        # message that names what to fix. It is checked HERE, before discovery, because
        # discovery from 0.0.0 selects every migration ever shipped.
        if effective_prev_version == Version("0.0.0") or effective_prev_version < MINIMUM_SUPPORTED_VERSION:
            self.validator.validate_version_support(effective_prev_version)
            return False

        # Discovery: Load migration classes dynamically
        self.migrations = self.discovery.discover_migrations(effective_prev_version, self.current_version, self)

        if self.migrations:
            if global_services_need_migration:
                self.output.print(
                    f"Global services & configuration: [fm.warn]v{self.prev_version}[/fm.warn] → [fm.ok]v{self.current_version}[/fm.ok]",
                    emoji_code="",
                )
                self.output.print("  • fm configuration", emoji_code="")
                self.output.print("  • shared services (mariadb, nginx-proxy)", emoji_code="")

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
                                f"  • {bench_name}: [fm.warn]v{bench_version}[/fm.warn] → [fm.ok]v{self.current_version}[/fm.ok]",
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
                        bench = MigrationBench(bench_name, bench_path, output=self.output)
                        if bench.running or bench.workers_running:
                            running.append(bench_name)
                if running:
                    self.output.warning(
                        f"The following target benches are currently running and will be restarted (containers recreated) during migration: {', '.join(running)}",
                    )
                    self.output.print(
                        "If you'd prefer no disruption, stop these benches (fm stop <bench>) and re-run migration.",
                    )
                    self.output.print("", emoji_code="")

            if not self.auto_proceed:
                continue_migration = self.output.prompt_ask(
                    prompt="Do you want to proceed?",
                    choices=[
                        {"name": "yes - Start migration", "value": "yes"},
                        {"name": "no - Abort and revert to previous fm version", "value": "no"},
                    ],
                    required_flag="--auto-proceed",
                )
            else:
                continue_migration = "yes"
                self.output.print("Proceeding with migration (--auto-proceed)", emoji_code="")

            if continue_migration == "no":
                self.output.print("", emoji_code="")
                self.output.print(
                    f"Migration aborted. To revert to v{self.prev_version.version!s}, run:",
                    emoji_code="",
                )
                self.output.print(f"  uv tool install frappe-manager=={self.prev_version.version!s}", emoji_code="")
                self.output.print("", emoji_code="")
                return False

        # Orchestration: Execute migrations with error handling.
        #
        # The undo stack is synced BEFORE each error handler runs, not only on success: a
        # migration that FAILED is exactly the one whose `down()` must run, and it is on the
        # orchestrator's stack (appended before `up()`). Syncing only on the success path left
        # the executor's copy empty on failure, so `_rollback_all` iterated nothing and
        # "Rollback complete." was printed with every backup unrestored and the half-migrated
        # state left in place.
        try:
            self.orchestrator.execute_migrations()
        except MigrationExceptionInBench as e:
            self.undo_stack = self.orchestrator.undo_stack
            return self.error_handler.handle_bench_migration_failure(e)
        except KeyboardInterrupt:
            # BaseException, so the handlers below never see it: Ctrl+C used to walk away
            # from a half-migrated host with no rollback, no halt and no record -- pressed,
            # of course, at exactly the moment a cutover looks hung and every bench is down.
            # Route it through the same --on-failure policy as any other failure (a second
            # Ctrl+C during the prompt still raises through, so dying on the spot remains
            # possible), then re-raise so the exit status stays an interrupt.
            self.undo_stack = self.orchestrator.undo_stack
            self.output.warning("Interrupted (Ctrl+C) mid-migration.")
            self.error_handler.handle_system_migration_failure(Exception("interrupted by Ctrl+C"))
            raise
        except Exception as e:
            self.undo_stack = self.orchestrator.undo_stack
            return self.error_handler.handle_system_migration_failure(e)

        self.undo_stack = self.orchestrator.undo_stack
        self.error_handler.finalize_success()
        # A HINT, never a prune: cleanup in fm is command-triggered only (`fm prune`,
        # `fm services prune`). A migration deleting backups as a side effect was tried
        # and rejected -- the operator decides when history goes.
        self._hint_backup_growth()
        return True

    def _hint_backup_growth(self):
        """After a successful run, say how much old backup history the touched locations
        carry beyond the configured retention, and which command trims it. Print-only."""
        from frappe_manager.migration_manager.backup_manager import CLI_MIGARATIONS_DIR
        from frappe_manager.utils.prune import dir_size, format_size, stale_sessions

        keep = self.fm_config_manager.prune.keep_backup_sessions
        hints: list[tuple[str, list[Path]]] = []
        if self.global_services_need_migration:
            hints.append(("fm services prune", stale_sessions(CLI_MIGARATIONS_DIR / "migrations", keep)))
        for bench_name, bench_data in self.migrate_benches.items():
            if bench_data["exception"] is None:
                root = CLI_BENCHES_DIRECTORY / bench_name / "backups" / "migrations"
                hints.append((f"fm prune {bench_name}", stale_sessions(root, keep)))

        for command, stale in hints:
            if not stale:
                continue
            size = sum(dir_size(p) for p in stale)
            self.output.print(
                f"{len(stale)} backup session(s) beyond the configured keep of {keep} "
                f"({format_size(size)}); trim with '{command}'",
                emoji_code="",
            )

    def set_bench_data(
        self,
        bench: MigrationBench,
        exception=None,
        migration_version: Version | None = None,
        traceback_str: str | None = None,
    ):
        self.migrate_benches[bench.name] = {
            "object": bench,
            "exception": exception,
            "last_migration_version": migration_version,
            "traceback": traceback_str,
        }

    def rollback(self):
        """
        Rollback the migration.

        DEPRECATED: Use orchestrator.rollback_migrations() instead.
        This method is kept for backward compatibility.
        """
        self.orchestrator.undo_stack = self.undo_stack
        self.orchestrator.rollback_migrations()
