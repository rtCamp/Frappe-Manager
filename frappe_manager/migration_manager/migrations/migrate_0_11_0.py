import importlib
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInSite
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.SiteManager import Site
from frappe_manager.site_manager.SiteManager import SiteManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.version import Version
from frappe_manager import CLI_DIR

class MigrationV0110(MigrationBase):
    version = Version("0.11.0")

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

        # migrate each site
        main_error = False

        for site_name, site_path in sites.items():
            site = Site(name=site_name, path=site_path.parent)
            if site.name in self.migration_executor.migrate_sites.keys():
                site_info =  self.migration_executor.migrate_sites[site.name]
                if site_info['exception']:
                    richprint.print(f"Skipping migration for failed site {site.name}.")
                    main_error = True
                    continue

            self.migration_executor.set_site_data(site,migration_version=self.version)
            try:
                self.migrate_site(site)
            except Exception as e:
                import traceback
                traceback_str = traceback.format_exc()
                self.logger.error(f"{site.name} [ EXCEPTION TRACEBACK ]:\n {traceback_str}")
                richprint.update_live()
                main_error = True
                self.migration_executor.set_site_data(site, e, self.version)
                self.undo_site_migrate(site)
                site.down(volumes=False,timeout=5)

        if main_error:
            raise MigrationExceptionInSite('')

        richprint.print(f"Successfull", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

    def migrate_site(self,site):
        richprint.print(f"Migrating site {site.name}", prefix=f"[bold]v{str(self.version)}:[/bold] ")

        # backup docker compose.yml
        self.backup_manager.backup(
            site.path / "docker-compose.yml", site_name=site.name
        )

        # backup common_site_config.json
        self.backup_manager.backup(
            site.path
            / "workspace"
            / "frappe-bench"
            / "sites"
            / "common_site_config.json",
            site_name=site.name,
        )

        site.down(volumes=False)
        self.migrate_site_compose(site)

    def down(self):
        # richprint.print(f"Started",prefix=f"[ Migration v{str(self.version)} ][ROLLBACK] : ")
        richprint.print(
            f"Started", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] "
        )
        self.logger.info("-" * 40)

        # undo each site
        for site, exception in  self.migration_executor.migrate_sites.items():
            if not exception:
                self.undo_site_migrate(site)

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup, force=True)

        richprint.print(
            f"Successfull", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] "
        )
        self.logger.info("-" * 40)

    def undo_site_migrate(self,site):

        for backup in self.backup_manager.backups:
            if backup.site == site.name:
                self.backup_manager.restore(backup, force=True)

        self.logger.info(f'Undo successfull for site: {site.name}')

    def migrate_site_compose(self, site: Site):

        status_msg = 'Migrating site compose'
        richprint.change_head(status_msg)

        compose_version = site.composefile.get_version()
        fm_version = importlib.metadata.version("frappe-manager")

        if not site.composefile.exists():
            richprint.print(f"{status_msg} {compose_version} -> {fm_version}: Failed ")
            raise MigrationExceptionInSite(f"{site.composefile.compose_path} not found.")

        # change image tag to the latest
        # in this migration only tag of frappe container is changed
        images_info = site.composefile.get_all_images()
        image_info = images_info['frappe']

        # get v0.11.0 frappe image
        image_info['tag'] = self.version.version_string()
        image_info['name'] = 'ghcr.io/rtcamp/frappe-manager-frappe'

        output = site.docker.pull(container_name=f"{image_info['name']}:{image_info['tag']}", stream=True)
        richprint.live_lines(output, padding=(0, 0, 0, 2))

        site.composefile.set_all_images(images_info)

        site.composefile.set_version(str(self.version))
        site.composefile.write_to_file()

        richprint.print(
                f"{status_msg} {compose_version} -> {fm_version}: Done"
        )
