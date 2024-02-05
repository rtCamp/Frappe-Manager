
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.site_manager.SiteManager import SiteManager
from frappe_manager.site_manager.site import Site


class MigrateCompose(MigrationBase):
    def __init__(self,sites_dir):
        self.sites_dir = sites_dir
        # super().__init__()

    def up(self):
        # take backup of each of the site docker compose
        sites_manager = SiteManager(self.sites_dir)
        sites = sites_manager.get_all_sites()

        # migrate each site
        for site_name,site_path in sites.items():
            site = Site(site_name,site_path)
            # take backup of the docker compose.yml
            self.backup_manager.backup(site.path)
            site.migrate_site_compose()

    def backup_site(self,site_name):
        site_path = self.sites_manager.get_site_path(site_name)
        self.backup_manager.backup(site_patjh

    def down(self):

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup)
