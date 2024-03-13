import shutil
from typing import Optional
import importlib
import pkgutil
from pathlib import Path
from rich.prompt import Prompt
from frappe_manager import CLI_DIR, CLI_SITES_ARCHIVE
from frappe_manager.metadata_manager import MetadataManager
from frappe_manager.migration_manager.migration_exections import (
    MigrationExceptionInSite,
)
from frappe_manager.utils.helpers import install_package, get_current_fm_version
from frappe_manager.logger import log
from frappe_manager.migration_manager.version import Version
from frappe_manager.display_manager.DisplayManager import richprint


class MigrationExecutor:
    """
    Migration executor class.

    This class is responsible for executing migrations.
    """

    def __init__(self):
        self.metadata_manager = MetadataManager()
        self.prev_version = self.metadata_manager.get_version()
        self.rollback_version = self.metadata_manager.get_version()
        self.current_version = Version(get_current_fm_version())
        self.migrations_path = Path(__file__).parent / "migrations"
        self.logger = log.get_logger()
        self.migrations = []
        self.undo_stack = []
        self.migrate_sites = {}

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
                    ):
                        migration = attr()
                        migration.set_migration_executor(migration_executor=self)
                        current_migration = migration
                        if (
                            migration.version > self.prev_version
                            and migration.version <= self.current_version
                        ):
                            # if not migration.skip:
                            self.migrations.append(migration)
                            # else:

            except Exception as e:
                print(f"Failed to register migration {name}: {e}")

        self.migrations = sorted(self.migrations, key=lambda x: x.version)

        if self.migrations:
            richprint.print("Pending Migrations...")

            for migration in self.migrations:
                richprint.print(f"[bold]MIGRATION:[/bold] v{migration.version}")

            richprint.print("This may take some time.", emoji_code=":light_bulb:")

            richprint.print(
                "Manual migration guide -> https://github.com/rtCamp/Frappe-Manager/wiki/Migrations#manual-migration-procedure",
                emoji_code=":light_bulb:",
            )

            migrate_msg = (
                "\n[blue]yes[/blue] : Start Migration."
                "\n[blue]no[/blue]  : Don't migrate and revert to previous fm version."
                "\nDo you want to migrate ?"
            )
            # prompt
            richprint.stop()
            continue_migration = Prompt.ask(migrate_msg, choices=["yes", "no"])

            if continue_migration == "no":
                install_package("frappe-manager", str(self.prev_version.version))
                richprint.exit(
                    f"Successfully installed [bold][blue]Frappe-Manager[/blue][/bold] version: v{str(self.prev_version.version)}",
                    emoji_code=":white_check_mark:",
                )

            richprint.start("Working")

        rollback = False
        archive = False

        try:
            # run all the migrations
            for migration in self.migrations:
                richprint.change_head(
                    f"Running migration introduced in v{migration.version}"
                )
                self.logger.info(f"[{migration.version}] : Migration starting")
                try:
                    self.undo_stack.append(migration)

                    migration.up()

                    if not self.rollback_version > migration.version:
                        self.rollback_version = migration.get_rollback_version()

                except MigrationExceptionInSite as e:
                    self.logger.error(f"[{migration.version}] : Migration Failed\n{e}")
                    if migration.version < self.migrations[-1].version:
                        continue
                    raise e

                except Exception as e:
                    self.logger.error(f"[{migration.version}] : Migration Failed\n{e}")
                    raise e

        except MigrationExceptionInSite as e:
            richprint.stop()
            if self.migrate_sites:
                richprint.print(
                    "[green]Migration was successfull on these sites.[/green]"
                )

                for site, site_status in self.migrate_sites.items():
                    if not site_status["exception"]:
                        richprint.print(f"[bold][green]SITE:[/green][/bold] {site}")

                richprint.print("[red]Migration failed on these sites[/red]")

                for site, site_status in self.migrate_sites.items():
                    if site_status["exception"]:
                        richprint.print(f"[bold][red]SITE[/red]:[/bold] {site}")
                        richprint.print(
                            f"[bold][red]FAILED MIGRATION VERSION[/red]:[/bold] {site_status['last_migration_version']}"
                        )
                        richprint.print(
                            f"[bold][red]EXCEPTION[/red]:[/bold] {type(site_status['exception']).__name__} - {site_status['exception']}"
                        )

                richprint.print(
                    f"More error details can be found in the log -> '{CLI_DIR}/logs/fm.log'"
                )

                archive_msg = (
                    f"\n[blue]yes[/blue] : Sites that have failed will be rolled back and stored in '{CLI_SITES_ARCHIVE}'."
                    "\n[blue]no[/blue]  : Revert the entire migration to the previous fm version."
                    "\nDo you wish to archive all sites that failed during migration?"
                )

                archive = Prompt.ask(archive_msg, choices=["yes", "no"])

                if archive == "no":
                    rollback = True

        except Exception as e:
            richprint.print(f"Migration failed: {e}")
            rollback = True

        if archive == "yes":
            self.prev_version = self.undo_stack[-1].version
            for site, site_info in self.migrate_sites.items():
                if site_info["exception"]:
                    archive_site_path = CLI_SITES_ARCHIVE / site
                    CLI_SITES_ARCHIVE.mkdir(exist_ok=True, parents=True)
                    shutil.move(site_info["object"].path, archive_site_path)
                    richprint.print(f"[bold]Archived site:[/bold] {site}")

        if rollback:
            richprint.start("Rollback")
            self.rollback()
            richprint.stop()
            self.metadata_manager.set_version(self.rollback_version)
            self.metadata_manager.save()
            richprint.print(
                f"Installing [bold][blue]Frappe-Manager[/blue][/bold] version: v{str(self.rollback_version.version)}"
            )
            install_package("frappe-manager", str(self.rollback_version.version))
            richprint.exit("Rollback complete.")

        self.metadata_manager.set_version(self.current_version)
        self.metadata_manager.save()
        return True

    def set_site_data(
        self, site, exception=None, migration_version: Optional[Version] = None, traceback_str: Optional[str] = None
    ):
        self.migrate_sites[site.name] = {
            "object": site,
            "exception": exception,
            "last_migration_version": migration_version,
            "traceback": traceback_str,
        }

    def get_site_data(self, site_name):
        try:
            data = self.migrate_sites[site_name]
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
                richprint.change_head(
                    f"Rolling back migration introduced in v{migration.version}"
                )
                self.logger.info(f"[{migration.version}] : Rollback starting")
                try:
                    migration.down()
                except Exception as e:
                    self.logger.error(f"[{migration.version}] : Rollback Failed\n{e}")
                    raise e
