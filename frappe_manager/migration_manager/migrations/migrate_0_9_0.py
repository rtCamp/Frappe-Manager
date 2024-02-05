import shutil
from frappe_manager.migration_manager.migration_base import MigrationBase

from frappe_manager import CLI_DIR
from frappe_manager.site_manager.SiteManager import SiteManager
from frappe_manager.migration_manager.version import Version
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.migration_executor import MigrationExecutor

class MigrationV090(MigrationBase):

    version = Version("0.9.0")

    def __init__(self):
        super().init()
        self.sitesdir = CLI_DIR / "sites"

        if self.sitesdir.exists():
            self.skip = True

    def set_migration_executor(self, migration_executor: MigrationExecutor):
        self.migration_executor = migration_executor

    def up(self):
        if self.skip:
            return True

        richprint.print(f"Started",prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

        move_directory_list = []
        for site_dir in CLI_DIR.iterdir():

            if site_dir.is_dir():
                docker_compose_path = site_dir / "docker-compose.yml"
                if docker_compose_path.exists():
                    move_directory_list.append(site_dir)

        # stop all the sites
        self.sitesdir.mkdir(parents=True, exist_ok=True)
        sites_mananger = SiteManager(CLI_DIR)
        sites_mananger.stop_sites()

        # move all the directories
        richprint.print(f"Moving sites from {CLI_DIR} to {self.sitesdir}",prefix=f"[bold]v{str(self.version)}:[/bold] ")

        for site in move_directory_list:
            site_name = site.parts[-1]
            new_path = self.sitesdir / site_name
            shutil.move(site, new_path)
            self.logger.debug(f"Moved:{site.exists()}")

        richprint.print(f"Successfull",prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info(f"[{self.version}] : Migration starting")
        self.logger.info("-" * 40)

    def down(self):
        if self.skip:
            return True

        # richprint.print(f"Started",prefix=f"[ Migration v{str(self.version)} ][ROLLBACK] : ")
        richprint.print(f"Started",prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)

        if self.sitesdir.exists():
            richprint.print(f"Found sites directory change.",prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")

            move_directory_list = []
            for site_dir in self.sitesdir.iterdir():

                if site_dir.is_dir():
                    docker_compose_path = site_dir / "docker-compose.yml"

                    if docker_compose_path.exists():
                        move_directory_list.append(site_dir)

            # stop all the sites
            sites_mananger = SiteManager(self.sitesdir)
            sites_mananger.stop_sites()

            # move all the directories
            for site in move_directory_list:
                site_name = site.parts[-1]
                new_path = self.sitesdir.parent / site_name
                shutil.move(site, new_path)

        # delete the sitedir
        shutil.rmtree(self.sitesdir)

        richprint.print(f"Successfull",prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)
