"""
Migration error handling, reporting, and recovery.

Handles failed migration scenarios including bench-specific failures,
archive operations, rollback coordination, and user-facing error reports.
"""

import shutil
from typing import TYPE_CHECKING

from frappe_manager import CLI_DIR, CLI_SITES_ARCHIVE
from frappe_manager.migration_manager.migration_exceptions import MigrationExceptionInBench

if TYPE_CHECKING:
    from frappe_manager.migration_manager.migration_executor import MigrationExecutor


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

        is_single_bench = self.executor.target_benches and len(self.executor.target_benches) == 1
        archive_decision = self._prompt_archive_or_rollback()

        if archive_decision == "yes":
            self._archive_failed_benches()
            return True
        if archive_decision == "skip":
            return False
        self._rollback_all(show_cli_downgrade_instructions=not is_single_bench)
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
        self.executor.output.display_error(f"[fm.error]Migration failed[/fm.error] : {exception}", emoji_code="")
        self._rollback_all()
        return False

    def _report_bench_results(self):
        """Report which benches passed and which failed migration."""
        passed_print_head = True

        # Report passed benches
        for bench, bench_status in self.executor.migrate_benches.items():
            if not bench_status["exception"]:
                if passed_print_head:
                    self.executor.output.print("\n\n[fm.ok]Migration Passed Benches[/fm.ok]\n", emoji_code="")
                    passed_print_head = False
                self.executor.output.print(f"[fm.ok]Bench[/fm.ok]: {bench}", emoji_code="")

        failed_print_head = True

        # Report failed benches
        for bench, bench_status in self.executor.migrate_benches.items():
            if bench_status["exception"]:
                if failed_print_head:
                    self.executor.output.print("\n[fm.error]Migration Failed Benches[/fm.error]\n", emoji_code="")
                    failed_print_head = False

                self.executor.output.display_error(f"[fm.error]Bench[/fm.error]: {bench}", emoji_code="")
                self.executor.output.display_error(
                    f"[fm.error]Failed Migration Version[/fm.error]: {bench_status['last_migration_version']}",
                    emoji_code="",
                )
                self.executor.output.display_error(
                    f"[fm.error]Exception[/fm.error]: {type(bench_status['exception']).__name__}",
                    emoji_code="",
                )
                self.executor.output.print(f"   {bench_status['exception']}", emoji_code="")

        self.executor.output.print(f"For error specifics, refer to {CLI_DIR}/logs/fm.log", emoji_code="")

        # Print separator
        if not failed_print_head or not passed_print_head:
            self.executor.output.print("=" * 60, emoji_code="")

    def _prompt_archive_or_rollback(self) -> str:
        """
        Prompt user to choose between archiving failed benches or rolling back all.

        Returns:
            str: "yes" to archive, "no" to rollback
        """
        target_benches = self.executor.target_benches
        is_single_bench = target_benches is not None and len(target_benches) == 1

        if is_single_bench and target_benches is not None:
            bench_name = target_benches[0]

            if self.executor.on_failure == "archive":
                self.executor.output.print(
                    "Warning: --on-failure=archive not supported for single bench migrations. Using rollback.",
                    emoji_code="",
                )

            if self.executor.on_failure == "rollback":
                self.executor.output.print("Rolling back bench (--on-failure=rollback)", emoji_code="")
                return "no"

            rollback_msg = [
                "Migration failed for bench.",
                "",
                "Available options:",
                "[fm.info][yes][/fm.info] Rollback bench : Restore the bench to its last working version before migration.",
                f"[fm.info][no][/fm.info] Skip rollback : Leave bench in current state. You can manually fix or retry with: fm migrate {bench_name}",
                "",
                "Do you want to rollback the bench?",
            ]

            rollback_decision = self.executor.output.prompt_ask(
                prompt="\n".join(rollback_msg),
                choices=["yes", "no"],
                required_flag="--on-failure",
            )

            if rollback_decision == "yes":
                return "no"
            self.executor.output.print("\nSkipping rollback. Bench remains in current state.", emoji_code="")
            self.executor.output.print(
                f"You can manually fix the bench or retry with: fm migrate {bench_name}",
                emoji_code="",
            )
            return "skip"

        if self.executor.on_failure == "archive":
            self.executor.output.print("Archiving failed benches (--on-failure=archive)", emoji_code="")
            return "yes"
        if self.executor.on_failure == "rollback":
            self.executor.output.print("Rolling back all benches (--on-failure=rollback)", emoji_code="")
            return "no"

        archive_msg = [
            "Available options after migrations failure :",
            rf"[fm.info][yes][/fm.info] Archive failed benches : Benches that have failed will be rolled back to there last successfully completed migration version and stored in '{CLI_SITES_ARCHIVE}'.",
            r"[fm.info][no][/fm.info] Revert migration : Restore the FM CLI and FM environment to the last successfully completed migration version for all benches.",
            "\nDo you wish to archive all benches that failed during migration ?",
        ]

        return self.executor.output.prompt_ask(
            prompt="\n".join(archive_msg),
            choices=["yes", "no"],
            required_flag="--on-failure",
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
                self.executor.output.print(f"[bold]Archived bench :[/bold] [fm.warn]{bench}[/fm.warn]", emoji_code="")

    def _rollback_all(self, show_cli_downgrade_instructions: bool = True):
        """
        Execute full rollback and update FM config version.

        Calls orchestrator's rollback, updates FM version, and optionally provides
        user instructions for CLI version rollback.

        Args:
            show_cli_downgrade_instructions: Whether to show pip install command
        """
        from frappe_manager.migration_manager.migration_orchestrator import MigrationOrchestrator

        orchestrator = MigrationOrchestrator(self.executor)
        orchestrator.undo_stack = self.executor.undo_stack
        orchestrator.rollback_migrations()

        self.executor.fm_config_manager.version = self.executor.rollback_version
        self.executor.fm_config_manager.export_to_toml()

        self.executor.output.print("", emoji_code="")
        self.executor.output.print("Rollback complete.", emoji_code="")

        if show_cli_downgrade_instructions:
            self.executor.output.print("", emoji_code="")
            self.executor.output.print(
                f"To revert FM CLI to v{self.executor.rollback_version.version!s}, run:",
                emoji_code="",
            )
            self.executor.output.print(
                f"  uv tool install frappe-manager=={self.executor.rollback_version.version!s}",
                emoji_code="",
            )

        self.executor.output.print("", emoji_code="")

    def finalize_success(self):
        """
        Update FM config version after successful migration.

        Called when all migrations complete successfully.
        """
        self.executor.fm_config_manager.version = self.executor.current_version
        self.executor.fm_config_manager.export_to_toml()
