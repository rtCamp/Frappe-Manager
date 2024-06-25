import shutil
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager import CLI_DIR
from frappe_manager.migration_manager.migration_helpers import MigrationBenches
from frappe_manager.migration_manager.version import Version
from frappe_manager.display_manager.DisplayManager import richprint


class MigrationV090(MigrationBase):
    version = Version("0.9.0")

    def init(self):
        self.benches_dir = CLI_DIR / "sites"
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)

        if self.benches_dir.exists():
            self.skip = True

    def up(self):
        if self.skip:
            return True

        richprint.stdout.rule(f':package: [bold][blue]v{str(self.version)}[/blue][bold]')
        self.logger.info(f"v{str(self.version)}: Started")
        self.logger.info("-" * 40)

        move_directory_list = []

        for site_dir in CLI_DIR.iterdir():
            if site_dir.is_dir():
                docker_compose_path = site_dir / "docker-compose.yml"

                if docker_compose_path.exists():
                    move_directory_list.append(site_dir)

        self.benches_dir.mkdir(parents=True, exist_ok=True)

        benches = MigrationBenches(self.benches_dir)
        benches.stop_benches()

        # move all the directories
        richprint.change_head(f"Moving benches from {CLI_DIR} to {self.benches_dir}")

        for site in move_directory_list:
            site_name = site.parts[-1]
            new_path = self.benches_dir / site_name
            shutil.move(site, new_path)
            self.logger.debug(f"Moved:{site.exists()}")

        richprint.print(f"Moved benches from {CLI_DIR} to {self.benches_dir}")
        self.logger.info("-" * 40)

    def down(self):
        if self.skip:
            return True

        richprint.change_head(f"Working on v{str(self.version)} rollback.")
        self.logger.info("-" * 40)

        if self.benches_dir.exists():
            move_directory_list = []
            for site_dir in self.benches_dir.iterdir():
                if site_dir.is_dir():
                    docker_compose_path = site_dir / "docker-compose.yml"

                    if docker_compose_path.exists():
                        move_directory_list.append(site_dir)

            # stop all the sites
            sites_mananger = MigrationBenches(self.benches_dir)
            sites_mananger.stop_benches()

            # move all the directories
            for site in move_directory_list:
                site_name = site.parts[-1]
                new_path = self.benches_dir.parent / site_name
                shutil.move(site, new_path)

        # delete the sitedir
        shutil.rmtree(self.benches_dir)

        richprint.print(f"[bold]v{str(self.version)}[/bold] rollback successfull.")
        self.logger.info("-" * 40)
