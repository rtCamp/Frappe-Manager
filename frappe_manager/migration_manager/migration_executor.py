import shutil
import typer
import os
import importlib
import configparser
import pkgutil
from pathlib import Path

import rich

from rich import inspect
from frappe_manager import CLI_DIR , CLI_SITES_ARCHIVE
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.metadata_manager import MetadataManager
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInSite
from frappe_manager.utils.helpers import downgrade_package, get_current_fm_version
from frappe_manager.logger import log
from frappe_manager.migration_manager.version import Version
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.display_manager.DisplayManager import richprint

class MigrationExecutor():
    """
    Migration executor class.

    This class is responsible for executing migrations.
    """

    def __init__(self):
        self.metadata_manager = MetadataManager()
        self.prev_version = self.metadata_manager.get_version()
        self.current_version = Version(get_current_fm_version())
        self.migrations_path = Path(__file__).parent / 'migrations'
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
        for (_, name, _) in pkgutil.iter_modules([str(self.migrations_path)]):
            try:
                module = importlib.import_module(f'.migrations.{name}', __package__)
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and hasattr(attr, 'up') and hasattr(attr, 'down') and hasattr(attr, 'set_migration_executor'):
                        migration = attr()
                        migration.set_migration_executor(migration_executor = self)
                        current_migration = migration
                        if migration.version > self.prev_version and migration.version <=  self.current_version:
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

            richprint.print(
                "This may take some time.", emoji_code=":light_bulb:"
            )

            richprint.print(
                "Manual migration guide can be found here -> https://github.com/rtCamp/Frappe-Manager/wiki/Migrations#manual-migration-procedure", emoji_code=":light_bulb:"
            )

            migrate_msg =(
                    "\nIF [y]: Start Migration."
                    "\nIF [N]: Don't migrate and revert to previous fm version."
                    "\nDo you want to migrate ?"
            )
            # prompt
            richprint.stop()
            continue_migration = typer.confirm(migrate_msg)

            if not continue_migration:
                downgrade_package('frappe-manager',str(self.prev_version.version))
                richprint.exit(f'Successfully installed [bold][blue]Frappe-Manager[/blue][/bold] version: v{str(self.prev_version.version)}',emoji_code=':white_check_mark:')

            richprint.start('Working')

        rollback = False
        archive = False

        try:
            # run all the migrations
            for migration in self.migrations:
                richprint.change_head(f"Running migration introduced in v{migration.version}")
                self.logger.info(f"[{migration.version}] : Migration starting")
                try:
                    self.undo_stack.append(migration)
                    migration.up()
                    self.prev_version = migration.version

                except Exception as e:
                    self.logger.error(f"[{migration.version}] : Migration Failed\n{e}")
                    raise e

        except MigrationExceptionInSite as e:
            richprint.stop()
            if self.migrate_sites:
                richprint.print("[green]Migration was successfull on these sites.[/green]")

                for site, exception in self.migrate_sites.items():
                    if not exception:
                        richprint.print(f"[bold][green]SITE:[/green][/bold] {site.name}")

                richprint.print("[red]Migration failed on these sites[/red]")

                for site, exception in self.migrate_sites.items():
                    if exception:
                        richprint.print(f"[bold][red]SITE[/red]:[/bold] {site.name}")
                        richprint.print(f"[bold][red]EXCEPTION[/red]:[/bold] {exception}")

                richprint.print(f"More details about the error can be found in the log -> `~/frappe/logs/fm.log`")

                archive_msg =(
                        f"\nIF [y]: Sites that have failed will be rolled back and stored in {CLI_SITES_ARCHIVE}."
                        "\nIF [N]: Revert the entire migration to the previous fm version."
                        "\nDo you wish to archive all sites that failed during migration?"
                )

                from rich.text import Text
                archive = typer.confirm(archive_msg)

                if not archive:
                    rollback = True

        except Exception as e:
            richprint.print(f"Migration failed: {e}")
            rollback = True


        if archive:
            self.prev_version = self.undo_stack[-1].version
            for site, exception in self.migrate_sites.items():
                if exception:
                    archive_site_path = CLI_SITES_ARCHIVE / site.name
                    CLI_SITES_ARCHIVE.mkdir(exist_ok=True, parents=True)
                    shutil.move(site.path,archive_site_path )
                    richprint.print(f"[bold]Archived site:[/bold] {site.name}")

        if rollback:
            richprint.start('Rollback')
            self.rollback()
            richprint.stop()
            self.metadata_manager.set_version(self.prev_version)
            self.metadata_manager.save()
            richprint.print(f"Installing [bold][blue]Frappe-Manager[/blue][/bold] version: v{str(self.prev_version.version)}")
            downgrade_package('frappe-manager',str(self.prev_version.version))
            richprint.exit("Rollback complete.")

        self.metadata_manager.set_version(self.prev_version)
        self.metadata_manager.save()

        return True

    def set_site_data(self,site,data = None):
        self.migrate_sites[site] = data

    def get_site_data(self,site):
        try:
            data = self.migrate_sites[site]
        except KeyError as e:
            return None

    def rollback(self):
        """
        Rollback the migration.
        This method will rollback the migration and return the number of
        rolled back statements.
        """

        # run all the migrations
        for migration in reversed(self.undo_stack):
            if migration.version > self.prev_version:
                richprint.change_head(f"Rolling back migration introduced in v{migration.version}")
                self.logger.info(f"[{migration.version}] : Rollback starting")
                try:
                    migration.down()
                except Exception as e:
                    self.logger.error(f"[{migration.version}] : Rollback Failed\n{e}")
                    raise e
