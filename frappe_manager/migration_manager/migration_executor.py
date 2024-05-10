import shutil
from typing import Optional
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from rich.padding import Padding
from rich.text import Text
import importlib
import pkgutil
from pathlib import Path
from frappe_manager import CLI_DIR, CLI_SITES_ARCHIVE
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.migration_exections import (
    MigrationExceptionInBench,
)
from frappe_manager.utils.helpers import capture_and_format_exception, install_package, get_current_fm_version
from frappe_manager.logger import log
from frappe_manager.migration_manager.version import Version
from frappe_manager.display_manager.DisplayManager import richprint


class MigrationExecutor:
    """
    Migration executor class.

    This class is responsible for executing migrations.
    """

    def __init__(self, fm_config_manager: FMConfigManager):
        self.fm_config_manager: FMConfigManager = fm_config_manager
        self.prev_version = self.fm_config_manager.version
        self.rollback_version = self.fm_config_manager.version
        self.current_version = Version(get_current_fm_version())
        self.migrations_path = Path(__file__).parent / "migrations"
        self.logger = log.get_logger()
        self.migrations = []
        self.undo_stack = []
        self.migrate_benches = {}

    def execute(self):
        """
        Execute the migration.
        This method will execute the migration and return the number of
        executed statements.
        """

        if not self.prev_version < self.current_version:
            return True

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
                            migration = attr()
                            migration.set_migration_executor(migration_executor=self)
                            current_migration = migration

                            if migration.version > self.prev_version and migration.version <= self.current_version:
                                self.migrations.append(migration)

            except Exception as e:
                exception_str = capture_and_format_exception()
                print(f"Failed to register migration {name}: {exception_str}")

        self.migrations = sorted(self.migrations, key=lambda x: x.version)

        if self.migrations:
            richprint.print("Pending Migrations...", emoji_code=':counterclockwise_arrows_button:')

            for migration in self.migrations:
                richprint.print(f"[bold]v{migration.version}[/bold]", emoji_code=':package:')

            richprint.print("This process may take a while.", emoji_code="\n:hourglass_not_done:")

            richprint.print(
                "For a manual migration guide, visit https://github.com/rtCamp/Frappe-Manager/wiki/Migrations#manual-migration-procedure",
                emoji_code=":blue_book:",
            )

            migrate_msg = [
                "\nOptions :\n",
                "[blue]\[yes][/blue] Start Migration: Proceed with the migration process.",
                "[blue]\[no][/blue]  Abort and Revert: Do not migrate and revert to the previous fm version.",
                "\nDo you want to proceed with the migration ?",
            ]
            continue_migration = richprint.prompt_ask(prompt="\n".join(migrate_msg), choices=["yes", "no"])

            if continue_migration == "no":
                install_package("frappe-manager", str(self.prev_version.version))
                richprint.exit(
                    f"Successfully installed [bold][blue]Frappe-Manager[/blue][/bold] version: v{str(self.prev_version.version)}",
                    emoji_code=":white_check_mark:",
                )

        rollback = False
        archive = False

        exception_migration_in_bench_occured = False
        try:
            # run all the migrations
            prev_migration = None
            for migration in self.migrations:
                richprint.change_head(f"Running migration introduced in v{migration.version}")
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
                            richprint.stdout.rule('[bold]Migration Passed Benches[bold]', style='green')
                            passed_print_head = False

                        richprint.print(f"[green]Bench[/green]: {bench}", emoji_code=':construction:')

                failed_print_head = True

                for bench, bench_status in self.migrate_benches.items():
                    if bench_status["exception"]:
                        if failed_print_head:
                            richprint.stdout.rule(
                                ':police_car_light: [bold][red]Migration Failed Benches[/red][bold] :police_car_light:',
                                style='red',
                            )
                            failed_print_head = False

                        richprint.error(f"[red]Bench[/red]: {bench}", emoji_code=':construction:')

                        richprint.error(
                            f"[red]Failed Migration Version[/red]: {bench_status['last_migration_version']}",
                            emoji_code=':package:',
                        )

                        richprint.error(
                            f"[red]Exception[/red]: {type(bench_status['exception']).__name__}",
                            emoji_code=':stop_sign:',
                        )
                        richprint.stdout.print(Padding(Text(text=str(bench_status['exception'])), (0, 0, 0, 3)))

                richprint.print(f"For error specifics, refer to {CLI_DIR}/logs/fm.log", emoji_code=':page_facing_up:')

                if not failed_print_head:
                    richprint.stdout.rule(style='red')
                else:
                    if not passed_print_head:
                        richprint.stdout.rule(style='green')

                archive_msg = [
                    'Available options after migrations failure :',
                    f"[blue]\[yes][/blue] Archive failed benches : Benches that have failed will be rolled back to there last successfully completed migration version and stored in '{CLI_SITES_ARCHIVE}'.",
                    '[blue]\[no][/blue] Revert migration : Restore the FM CLI and FM environment to the last successfully completed migration version for all benches.',
                    '\nDo you wish to archive all benches that failed during migration ?',
                ]
                archive = richprint.prompt_ask(prompt="\n".join(archive_msg), choices=["yes", "no"])

                if archive == "no":
                    rollback = True

        except Exception as e:
            richprint.error(f"[red]Migration failed[red] : {e}")
            rollback = True

        if archive == "yes":
            self.prev_version = self.undo_stack[-1].version
            for bench, bench_info in self.migrate_benches.items():
                if bench_info["exception"]:
                    archive_bench_path = CLI_SITES_ARCHIVE / bench
                    CLI_SITES_ARCHIVE.mkdir(exist_ok=True, parents=True)
                    shutil.move(bench_info["object"].path, archive_bench_path)
                    richprint.print(f"[bold]Archived bench :[/bold] [yellow]{bench}[/yellow]")

        if rollback:
            self.rollback()
            self.fm_config_manager.version = self.rollback_version
            self.fm_config_manager.export_to_toml()
            richprint.print(
                f"Installing [bold][blue]Frappe-Manager[/blue][/bold] version: v{str(self.rollback_version.version)}"
            )
            install_package("frappe-manager", str(self.rollback_version.version))
            richprint.exit("Rollback complete.", emoji_code=':back:')

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

    def rollback(self):
        """
        Rollback the migration.
        This method will rollback the migration and return the number of
        rolled back statements.
        """

        # run all the migrations
        for migration in reversed(self.undo_stack):
            if migration.version > self.rollback_version:
                richprint.change_head(f"Rolling back migration introduced in v{migration.version}")
                self.logger.info(f"[{migration.version}] : Rollback starting")
                try:
                    migration.down()
                except Exception as e:
                    self.logger.error(f"[{migration.version}] : Rollback Failed\n{e}")
                    raise e
