"""
Migration error handling, reporting, and recovery.

Handles failed migration scenarios including bench-specific failures,
archive operations, rollback coordination, and user-facing error reports.
"""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Any
from frappe_manager import CLI_DIR, CLI_SITES_ARCHIVE
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.version import Version

if TYPE_CHECKING:
    from frappe_manager.migration_manager.migration_executor import MigrationExecutor
    from frappe_manager.output_manager import OutputHandler


class MigrationErrorHandler:
    """
    Handles migration failures, error reporting, and recovery operations.

    Responsibilities:
    - Report failed/passed benches
    - Prompt user for archive vs rollback decision
    - Execute archiving of failed benches
    - Coordinate rollback operations
    - Update FM version after recovery
    """

    def __init__(self, executor: "MigrationExecutor"):
        self.executor = executor

    def handle_bench_migration_failure(self, exception: MigrationExceptionInBench) -> bool:
        """
        Handle MigrationExceptionInBench - partial bench migration failures.

        Reports which benches passed/failed, prompts user for recovery action,
        and executes either archive or rollback.

        Args:
            exception: The bench migration exception that was raised

        Returns:
            bool: True if recovery succeeded, False if rollback was required
        """
        if not self.executor.migrate_benches:
            return False

        self._report_bench_results()

        archive_decision = self._prompt_archive_or_rollback()

        if archive_decision == "yes":
            self._archive_failed_benches()
            return True
        else:
            self._rollback_all()
            return False

    def handle_system_migration_failure(self, exception: Exception) -> bool:
        """
        Handle system-level migration failure.

        Reports error and triggers rollback.

        Args:
            exception: The exception that caused system migration failure

        Returns:
            bool: Always False (migration failed)
        """
        self.executor.output.display_error(f"[red]Migration failed[red] : {exception}", emoji_code="")
        self._rollback_all()
        return False

    def _report_bench_results(self):
        """Report which benches passed and which failed migration."""
        passed_print_head = True

        # Report passed benches
        for bench, bench_status in self.executor.migrate_benches.items():
            if not bench_status["exception"]:
                if passed_print_head:
                    self.executor.output.print("\n\n[bold green]Migration Passed Benches[/bold green]\n", emoji_code="")
                    passed_print_head = False
                self.executor.output.print(f"[green]Bench[/green]: {bench}", emoji_code="")

        failed_print_head = True

        # Report failed benches
        for bench, bench_status in self.executor.migrate_benches.items():
            if bench_status["exception"]:
                if failed_print_head:
                    self.executor.output.print("\n[bold red]Migration Failed Benches[/bold red]\n", emoji_code="")
                    failed_print_head = False

                self.executor.output.display_error(f"[red]Bench[/red]: {bench}", emoji_code="")
                self.executor.output.display_error(
                    f"[red]Failed Migration Version[/red]: {bench_status['last_migration_version']}",
                    emoji_code="",
                )
                self.executor.output.display_error(
                    f"[red]Exception[/red]: {type(bench_status['exception']).__name__}",
                    emoji_code="",
                )
                self.executor.output.print(f"   {bench_status['exception']}", emoji_code="")

        self.executor.output.print(f"For error specifics, refer to {CLI_DIR}/logs/fm.log", emoji_code="")

        # Print separator
        if not failed_print_head:
            self.executor.output.print("=" * 60, emoji_code="")
        else:
            if not passed_print_head:
                self.executor.output.print("=" * 60, emoji_code="")

    def _prompt_archive_or_rollback(self) -> str:
        """
        Prompt user to choose between archiving failed benches or rolling back all.

        Returns:
            str: "yes" to archive, "no" to rollback
        """
        is_single_bench = self.executor.target_benches and len(self.executor.target_benches) == 1

        if is_single_bench:
            if self.executor.on_failure == "archive":
                self.executor.output.print(
                    "Warning: --on-failure=archive not supported for single bench migrations. Rolling back instead.",
                    emoji_code="",
                )
                return "no"
            elif self.executor.on_failure == "rollback":
                self.executor.output.print("Rolling back bench (--on-failure=rollback)", emoji_code="")
                return "no"
            else:
                self.executor.output.print(
                    "Migration failed. Bench will be rolled back to previous version.", emoji_code=""
                )
                self.executor.output.print(
                    f"You can retry migration with: fm migrate {self.executor.target_benches[0]}", emoji_code=""
                )
                return "no"

        if self.executor.on_failure == "archive":
            self.executor.output.print("Archiving failed benches (--on-failure=archive)", emoji_code="")
            return "yes"
        elif self.executor.on_failure == "rollback":
            self.executor.output.print("Rolling back all benches (--on-failure=rollback)", emoji_code="")
            return "no"

        archive_msg = [
            'Available options after migrations failure :',
            rf"[blue][yes][/blue] Archive failed benches : Benches that have failed will be rolled back to there last successfully completed migration version and stored in '{CLI_SITES_ARCHIVE}'.",
            r'[blue][no][/blue] Revert migration : Restore the FM CLI and FM environment to the last successfully completed migration version for all benches.',
            '\nDo you wish to archive all benches that failed during migration ?',
        ]

        return self.executor.output.prompt_ask(
            prompt="\n".join(archive_msg), choices=["yes", "no"], required_flag='--on-failure'
        )

    def _archive_failed_benches(self):
        """
        Archive all failed benches to CLI_SITES_ARCHIVE.

        Updates executor's prev_version and moves failed bench directories.
        """
        self.executor.prev_version = self.executor.undo_stack[-1].version

        for bench, bench_info in self.executor.migrate_benches.items():
            if bench_info["exception"]:
                archive_bench_path = CLI_SITES_ARCHIVE / bench
                CLI_SITES_ARCHIVE.mkdir(exist_ok=True, parents=True)
                shutil.move(bench_info["object"].path, archive_bench_path)
                self.executor.output.print(f"[bold]Archived bench :[/bold] [yellow]{bench}[/yellow]", emoji_code="")

    def _rollback_all(self):
        """
        Execute full rollback and update FM config version.

        Calls orchestrator's rollback, updates FM version, and provides
        user instructions for CLI version rollback.
        """
        from frappe_manager.migration_manager.migration_orchestrator import MigrationOrchestrator

        orchestrator = MigrationOrchestrator(self.executor)
        orchestrator.undo_stack = self.executor.undo_stack
        orchestrator.rollback_migrations()

        self.executor.fm_config_manager.version = self.executor.rollback_version
        self.executor.fm_config_manager.export_to_toml()

        self.executor.output.print("", emoji_code="")
        self.executor.output.print("Rollback complete.", emoji_code="")
        self.executor.output.print("", emoji_code="")
        self.executor.output.print(
            f"To revert FM CLI to v{str(self.executor.rollback_version.version)}, run:", emoji_code=""
        )
        self.executor.output.print(
            f"  uv tool install frappe-manager=={str(self.executor.rollback_version.version)}", emoji_code=""
        )
        self.executor.output.print("", emoji_code="")

    def finalize_success(self):
        """
        Update FM config version after successful migration.

        Called when all migrations complete successfully.
        """
        self.executor.fm_config_manager.version = self.executor.current_version
        self.executor.fm_config_manager.export_to_toml()
