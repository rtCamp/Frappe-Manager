import importlib
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInSite
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.SiteManager import Site
from frappe_manager.site_manager.SiteManager import SiteManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.version import Version
from frappe_manager import CLI_DIR


class MigrationV0120(MigrationBase):
    version = Version("0.12.0")

    def __init__(self):
        super().init()
        self.sites_dir = CLI_DIR / "sites"
        self.services_manager = ServicesManager(verbose=False)

    def set_migration_executor(self, migration_executor: MigrationExecutor):
        self.migration_executor = migration_executor

    def up(self):
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

        # take backup of each of the site docker compose
        sites_manager = SiteManager(self.sites_dir)
        sites_manager.stop_sites()
        sites = sites_manager.get_all_sites()

        # Pulling latest image

        self.image_info = {"tag": self.version.version_string(), "name": "ghcr.io/rtcamp/frappe-manager-frappe"}
        pull_image = f"{self.image_info['name']}:{self.image_info['tag']}"

        richprint.change_head(f"Pulling Image {pull_image}")
        output = DockerClient().pull(container_name=pull_image, stream=True)
        richprint.live_lines(output, padding=(0, 0, 0, 2))
        richprint.print(f"Image pulled [blue]{pull_image}[/blue]")

        # migrate each site
        main_error = False

        for site_name, site_path in sites.items():
            site = Site(name=site_name, path=site_path.parent)
            if site.name in self.migration_executor.migrate_sites.keys():
                site_info = self.migration_executor.migrate_sites[site.name]
                if site_info["exception"]:
                    richprint.print(f"Skipping migration for failed site {site.name}.")
                    main_error = True
                    continue

            self.migration_executor.set_site_data(site, migration_version=self.version)
            try:
                self.migrate_site(site)
            except Exception as e:
                import traceback

                traceback_str = traceback.format_exc()
                self.logger.error(f"{site.name} [ EXCEPTION TRACEBACK ]:\n {traceback_str}")
                richprint.update_live()
                main_error = True
                self.migration_executor.set_site_data(site, e, self.version, traceback_str=traceback_str)
                self.undo_site_migrate(site)
                site.down(volumes=False, timeout=5)

        if main_error:
            raise MigrationExceptionInSite("")

        richprint.print(f"Successfull", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

    def migrate_site(self, site):
        richprint.print(f"Migrating site {site.name}", prefix=f"[bold]v{str(self.version)}:[/bold] ")

        # backup docker compose.yml
        self.backup_manager.backup(site.path / "docker-compose.yml", site_name=site.name)

        # backup common_site_config.json
        self.backup_manager.backup(
            site.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json",
            site_name=site.name,
        )

        site.down(volumes=False)
        self.migrate_site_compose(site)

    def down(self):
        # richprint.print(f"Started",prefix=f"[ Migration v{str(self.version)} ][ROLLBACK] : ")
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)

        # undo each site
        for site, exception in self.migration_executor.migrate_sites.items():
            if not exception:
                self.undo_site_migrate(site)

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup, force=True)

        richprint.print(f"Successfull", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] ")
        self.logger.info("-" * 40)

    def undo_site_migrate(self, site):
        for backup in self.backup_manager.backups:
            if backup.site == site.name:
                self.backup_manager.restore(backup, force=True)

        self.logger.info(f"Undo successfull for site: {site.name}")

    def migrate_site_compose(self, site: Site):
        status_msg = "Migrating site compose"
        richprint.change_head(status_msg)

        compose_version = site.composefile.get_version()
        fm_version = importlib.metadata.version("frappe-manager")

        if not site.composefile.exists():
            richprint.print(f"{status_msg} {compose_version} -> {fm_version}: Failed ")
            raise MigrationExceptionInSite(f"{site.composefile.compose_path} not found.")

        images_info = site.composefile.get_all_images()

        # for all services
        images_info["frappe"] = self.image_info
        images_info["socketio"] = self.image_info
        images_info["schedule"] = self.image_info

        # workers image set
        workers_info = site.workers.composefile.get_all_images()
        for worker in workers_info.keys():
            workers_info[worker] = self.image_info

        site.workers.composefile.set_all_images(workers_info)
        site.composefile.set_all_images(images_info)

        compose_yml = site.composefile.yml
        worker_compose_yml = site.workers.composefile.yml

        # remove restart: from all the services
        for service in compose_yml["services"]:
            try:
                del compose_yml["services"][service]["restart"]
            except KeyError as e:
                self.logger.error(f"{site.name}: Not able to delete restart: always attribute from compose file.{e}")
                pass

        for service in worker_compose_yml["services"]:
            try:
                del worker_compose_yml["services"][service]["restart"]
            except KeyError as e:
                self.logger.error(f"{site.name} worker: Not able to delete restart: always attribute from compose file.{e}")
                pass

        richprint.print("Removed [blue]restart: always[/blue]")

        site.workers.composefile.set_version(str(self.version))
        site.workers.composefile.write_to_file()

        site.composefile.set_version(str(self.version))
        site.composefile.write_to_file()

        richprint.print(f"{status_msg} {compose_version} -> {fm_version}: Done")
