import shutil
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager import CLI_DIR
from frappe_manager.migration_manager.migration_helpers import MigrationBenches
from frappe_manager.migration_manager.version import Version
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.migration_executor import MigrationExecutor

class MigrationV090(MigrationBase):

    version = Version("0.9.0")

    def __init__(self):
        super().init()
        self.bences_dir = CLI_DIR / "sites"

        if self.bences_dir.exists():
            self.skip = True

    # def set_migration_executor(self, migration_executor: MigrationExecutor):
    #     self.migration_executor = migration_executor

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

        self.bences_dir.mkdir(parents=True, exist_ok=True)

        benches = MigrationBenches(self.bences_dir)
        benches.stop_benches()

        # move all the directories
        richprint.print(f"Moving sites from {CLI_DIR} to {self.bences_dir}",prefix=f"[bold]v{str(self.version)}:[/bold] ")

        for site in move_directory_list:
            site_name = site.parts[-1]
            new_path = self.bences_dir / site_name
            shutil.move(site, new_path)
            self.logger.debug(f"Moved:{site.exists()}")

        richprint.print(f"Successfull",prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info(f"[{self.version}] : Migration starting")
        self.logger.info("-" * 40)

    def down(self):
        if self.skip:
            return True

        richprint.print(f"Started",prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)

        if self.bences_dir.exists():
            richprint.print(f"Found sites directory change.",prefix=f"[bold]v{str(self.version.version)} [ROLLBACK]:[/bold] ")

            move_directory_list = []
            for site_dir in self.bences_dir.iterdir():

                if site_dir.is_dir():
                    docker_compose_path = site_dir / "docker-compose.yml"

                    if docker_compose_path.exists():
                        move_directory_list.append(site_dir)

            # stop all the sites
            sites_mananger = MigrationBenches(self.bences_dir)
            sites_mananger.stop_benches()

            # move all the directories
            for site in move_directory_list:
                site_name = site.parts[-1]
                new_path = self.bences_dir.parent / site_name
                shutil.move(site, new_path)

        # delete the sitedir
        shutil.rmtree(self.bences_dir)

        richprint.print(f"Successfull",prefix=f"[bold]v{str(self.version.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)
