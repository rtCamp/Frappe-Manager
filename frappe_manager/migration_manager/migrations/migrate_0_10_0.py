import shutil
from copy import deepcopy
from pathlib import Path
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.migration_manager.backup_manager import BackupData
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager import CLI_DIR
from frappe_manager.migration_manager.migration_exections import (
    MigrationExceptionInSite,
)
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.site_exceptions import (
    SiteDatabaseAddUserException,
    SiteDatabaseExport,
    SiteDatabaseStartTimeout,
)
from frappe_manager.utils.docker import host_run_cp
from frappe_manager.site_manager.SiteManager import Site
from frappe_manager.site_manager.SiteManager import SiteManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import get_container_name_prefix
from frappe_manager.migration_manager.version import Version
from datetime import datetime


class MigrationV0100(MigrationBase):
    version = Version("0.10.0")

    def __init__(self):
        super().init()
        self.sites_dir = CLI_DIR / "sites"
        self.services_manager = ServicesManager(verbose=False)
        self.string_timestamp = datetime.now().strftime("%d-%b-%y--%H-%M-%S")

    def get_rollback_version(self):
        # this was without any migrations
        return Version("0.10.1")

    def set_migration_executor(self, migration_executor: MigrationExecutor):
        self.migration_executor = migration_executor

    def up(self):
        richprint.print(f"Started", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

        # take backup of each of the site docker compose
        sites_manager = SiteManager(self.sites_dir)
        sites_manager.stop_sites()
        sites = sites_manager.get_all_sites()

        # create services
        self.services_manager.init()
        self.services_manager.entrypoint_checks()
        self.services_manager.down(volumes=False)
        self.services_manager.start(service="global-db")

        # migrate each site
        main_error = False

        for site_name, site_path in sites.items():
            site = Site(name=site_name, path=site_path.parent)
            self.migration_executor.set_site_data(site, migration_version=self.version)
            try:
                self.migrate_site(site)
            except Exception as e:
                import traceback

                traceback_str = traceback.format_exc()
                self.logger.error(f"[ EXCEPTION TRACEBACK ]:\n {traceback_str}")
                richprint.update_live()
                main_error = True
                self.migration_executor.set_site_data(site, e, self.version)
                self.undo_site_migrate(site)
                site.down(volumes=False, timeout=5)

        if main_error:
            raise MigrationExceptionInSite("")

        # new bind mount is introudced so create it
        richprint.print(f"Successfull", prefix=f"[bold]v{str(self.version)}:[/bold] ")
        self.logger.info("-" * 40)

    def migrate_site(self, site):
        richprint.print(
            f"Migrating site {site.name}", prefix=f"[bold]v{str(self.version)}:[/bold] "
        )

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

        site.down(volumes=False)

        site_db_info = site.get_site_db_info()
        site_db_name = site_db_info["name"]
        site_db_user = site_db_info["user"]
        site_db_pass = site_db_info["password"]

        self.services_manager.add_user(site_db_name, site_db_user, site_db_pass)

    def down(self):
        richprint.print(
            f"Started", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] "
        )
        self.logger.info("-" * 40)

        self.services_manager.down()

        if self.services_manager.path.exists():
            self.services_manager.remove_itself()

        sites_manager = SiteManager(self.sites_dir)
        sites = sites_manager.get_all_sites()

        # undo each site
        for site, exception in self.migration_executor.migrate_sites.items():
            if not exception:
                self.undo_site_migrate(site)

        for backup in self.backup_manager.backups:
            self.backup_manager.restore(backup, force=True)

        richprint.print(
            f"Successfull", prefix=f"[bold]v{str(self.version)} [ROLLBACK]:[/bold] "
        )
        self.logger.info("-" * 40)

    def undo_site_migrate(self, site):
        for backup in self.backup_manager.backups:
            if backup.site == site.name:
                self.backup_manager.restore(backup, force=True)

        configs_backup = site.path / f"configs-{self.string_timestamp}.bak"

        configs_path = site.path / "configs"

        if configs_path.exists():
            shutil.rmtree(configs_path)

        if configs_backup.exists():
            shutil.copytree(configs_backup, configs_path)
            shutil.rmtree(configs_backup)
            self.logger.info(f"Removed : {configs_backup}")

        service = "mariadb"
        services_list = []
        services_list.append(service)

        # start each site and forcefully add user to the site
        try:
            output = site.docker.compose.up(services=services_list, stream=True)
            # output =  site.docker.run(ser)
            richprint.live_lines(output, padding=(0, 0, 0, 2))
            try:
                site.add_user("mariadb", "root", "root", force=True)
            except SiteDatabaseAddUserException as e:
                pass
            site.down(volumes=False)
        except DockerException as e:
            pass

        self.logger.info(f"Undo successfull for site: {site.name}")

    def migrate_site_compose(self, site: Site):
        richprint.change_head("Migrating database")
        compose_version = site.composefile.get_version()

        if not site.composefile.exists():
            richprint.print(
                f"{status_msg} {compose_version} -> {self.version.version}: Failed "
            )
            return

        # export db
        db_backup_file = self.db_migration_export(site)

        # backup site_db
        db_backup = self.backup_manager.backup(
            db_backup_file, site_name=site.name, allow_restore=False
        )

        self.db_migration_import(site=site, db_backup_file=db_backup)

        status_msg = "Migrating site compose"
        richprint.change_head(status_msg)

        # get all the payloads
        envs = site.composefile.get_all_envs()
        labels = site.composefile.get_all_labels()

        # introduced in v0.10.0
        if not "ENVIRONMENT" in envs["frappe"]:
            envs["frappe"]["ENVIRONMENT"] = "dev"

        envs["frappe"]["CONTAINER_NAME_PREFIX"] = get_container_name_prefix(site.name)
        envs["frappe"]["MARIADB_ROOT_PASS"] = "root"
        envs["frappe"]["MARIADB_HOST"] = "global-db"

        envs["nginx"]["VIRTUAL_HOST"] = site.name

        envs["adminer"] = {"ADMINER_DEFAULT_SERVER": "global-db"}

        import os

        envs_user_info = {}
        userid_groupid: dict = {"USERID": os.getuid(), "USERGROUP": os.getgid()}

        env_user_info_container_list = ["frappe", "schedule", "socketio"]

        for env in env_user_info_container_list:
            envs_user_info[env] = deepcopy(userid_groupid)

        # overwrite user for each invocation
        users = {"nginx": {"uid": os.getuid(), "gid": os.getgid()}}

        self.create_compose_dirs(site)

        site.composefile.template_name = "docker-compose.migration.tmpl"

        # load template
        site.composefile.yml = site.composefile.load_template()

        # set all the payload
        site.composefile.set_all_envs(envs)
        site.composefile.set_all_envs(envs_user_info)
        site.composefile.set_all_labels(labels)
        site.composefile.set_all_users(users)
        # site.composefile.set_all_extrahosts(extrahosts)

        site.composefile.remove_secrets_from_container("frappe")
        site.composefile.remove_root_secrets_compose()
        site.composefile.set_network_alias("nginx", "site-network", [site.name])
        site.composefile.set_container_names(get_container_name_prefix(site.name))

        site.composefile.set_version(str(self.version))
        site.composefile.set_top_networks_name(
            "site-network", get_container_name_prefix(site.name)
        )
        site.composefile.write_to_file()

        # change the node socketio port
        site.common_site_config_set({"socketio_port": "80"})

        richprint.print(
            f"{status_msg} {compose_version} -> {self.version.version}: Done"
        )

        return db_backup

    def create_compose_dirs(self, site):
        #### directory creation
        configs_path = site.path / "configs"

        # custom config directory found moving it
        # check if config directory exits if exists then move it
        if configs_path.exists():
            backup_path = f"{configs_path.absolute()}.{self.string_timestamp}.bak"
            shutil.move(configs_path, backup_path)

        configs_path.mkdir(parents=True, exist_ok=True)

        # create nginx dirs
        nginx_dir = configs_path / "nginx"
        nginx_dir.mkdir(parents=True, exist_ok=True)

        nginx_poluate_dir = ["conf"]

        nginx_image = site.composefile.yml["services"]["nginx"]["image"]

        for directory in nginx_poluate_dir:
            new_dir = nginx_dir / directory
            if not new_dir.exists():
                new_dir_abs = str(new_dir.absolute())
                host_run_cp(
                    nginx_image,
                    source="/etc/nginx",
                    destination=new_dir_abs,
                    docker=site.docker,
                )

        nginx_subdirs = ["logs", "cache", "run"]

        for directory in nginx_subdirs:
            new_dir = nginx_dir / directory
            new_dir.mkdir(parents=True, exist_ok=True)

    def is_database_started(
        self,
        site_name,
        docker_object,
        db_user="root",
        db_password="root",
        db_host="127.0.0.1",
        service="mariadb",
        interval=5,
        timeout=30,
    ):
        import time

        i = 0

        check_connection_command = f"/usr/bin/mariadb -h{db_host} -u{db_user} -p'{db_password}' -e 'SHOW DATABASES;'"
        connected = False
        error = None

        while i < timeout:
            try:
                time.sleep(interval)
                output = docker_object.compose.exec(
                    service,
                    command=check_connection_command,
                    stream=True,
                    stream_only_exit_code=True,
                )
                connected = True
                break
            except DockerException as e:
                self.logger.error(f"[db start check] try: {i} got exception {e}")
                error = e
                pass
            i += 1

        if not connected:
            raise SiteDatabaseStartTimeout(site_name, f"Not able to start db: {error}")

    def db_migration_export(self, site) -> Path:
        self.logger.debug("[db export] site: %s", site.name)
        try:
            # if site.composefile.exists():

            # DB MIGRATION if version < 1 and site.config exits
            # start the site
            output = site.docker.compose.up(
                services=["mariadb", "frappe"], detach=True, pull="missing", stream=True
            )

            richprint.live_lines(output, padding=(0, 0, 0, 2))

            self.logger.debug("[db export] checking if mariadb started")

            self.is_database_started(site.name, site.docker)

            # create dir to store migration
            db_migration_dir_path = site.path / "workspace" / "migrations"
            db_migration_dir_path.mkdir(exist_ok=True)

            from datetime import datetime

            current_datetime = datetime.now()
            formatted_date = current_datetime.strftime("%d-%m-%Y--%H-%M-%S")

            db_migration_file_path = (
                f"/workspace/migrations/db-{site.name}-{formatted_date}.sql"
            )

            site_db_info = site.get_site_db_info()
            site_db_name = site_db_info["name"]

            db_backup_command = f"mysqldump -uroot -proot -h'mariadb' -P3306 {site_db_name} --result-file={db_migration_file_path}"  # db_backup_command = f"mysqldump -uroot -proot -h'mariadb' -p3306 {site_db_name} {db_migration_file_path}"

            # backup the db
            output_backup_db = site.docker.compose.exec(
                "frappe",
                command=db_backup_command,
                stream=True,
                workdir="/workspace/frappe-bench",
                user="frappe",
                stream_only_exit_code=True,
            )

            output_stop = site.docker.compose.stop(timeout=10, stream=True)

            site_db_migration_file_path = Path(site.path / db_migration_file_path[1:])

            return site_db_migration_file_path

        except Exception as e:
            raise SiteDatabaseExport(site.name, f"Error while exporting db: {e}")

    def db_migration_import(self, site: Site, db_backup_file: BackupData):
        self.logger.info(
            f"[database import: global-db] {site.name} -> {db_backup_file}"
        )

        # cp into the global contianer
        self.services_manager.docker.compose.cp(
            source=str(db_backup_file.src.absolute()),
            destination=f"global-db:/tmp/{db_backup_file.src.name}",
            stream=True,
            stream_only_exit_code=True,
        )

        services_db_info = self.services_manager.get_database_info()
        services_db_user = services_db_info["user"]
        services_db_pass = services_db_info["password"]
        services_db_host = "127.0.0.1"

        site_db_info = site.get_site_db_info()
        site_db_name = site_db_info["name"]
        site_db_user = site_db_info["user"]
        site_db_pass = site_db_info["password"]

        mariadb_command = f"/usr/bin/mariadb -u{services_db_user} -p'{services_db_pass}' -h'{services_db_host}' -P3306  -e "
        mariadb = f"/usr/bin/mariadb -u{services_db_user} -p'{services_db_pass}' -h'{services_db_host}' -P3306"

        self.is_database_started(
            site.name,
            self.services_manager.docker,
            service="global-db",
            db_user=services_db_user,
            db_password=services_db_pass,
        )

        db_add_database = (
            mariadb_command + f"'CREATE DATABASE IF NOT EXISTS `{site_db_name}`';"
        )

        output_add_db = self.services_manager.docker.compose.exec(
            "global-db",
            command=db_add_database,
            stream=True,
            stream_only_exit_code=True,
        )

        db_remove_user = mariadb_command + f"'DROP USER `{site_db_user}`@`%`;'"

        error = None
        removed = False
        try:
            output = self.services_manager.docker.compose.exec(
                "global-db",
                command=db_remove_user,
                stream=True,
                stream_only_exit_code=True,
            )
        except DockerException as e:
            error = e
            removed = False

            if "error 1396" in str(e.stderr).lower():
                removed = True

        if removed:
            db_add_user = (
                mariadb_command
                + f"'CREATE USER `{site_db_user}`@`%` IDENTIFIED BY \"{site_db_pass}\";'"
            )

            output_add_user_db = self.services_manager.docker.compose.exec(
                "global-db",
                command=db_add_user,
                stream=True,
                stream_only_exit_code=True,
            )

            db_grant_user = (
                mariadb_command
                + f"'GRANT ALL PRIVILEGES ON `{site_db_name}`.* TO `{site_db_user}`@`%`;'"
            )

            output_grant_user_db = self.services_manager.docker.compose.exec(
                "global-db",
                command=db_grant_user,
                stream=True,
                stream_only_exit_code=True,
            )

            db_import_command = (
                mariadb + f" {site_db_name} -e 'source /tmp/{db_backup_file.src.name}'"
            )

            output_import_db = self.services_manager.docker.compose.exec(
                "global-db",
                command=db_import_command,
                stream=True,
                stream_only_exit_code=True,
            )

            check_connection_command = mariadb_command + f"'SHOW DATABASES;'"

            output_check_db = self.services_manager.docker.compose.exec(
                "global-db",
                command=check_connection_command,
                stream=True,
                stream_only_exit_code=True,
            )
        else:
            raise SiteDatabaseAddUserException(
                site.name, f"Database user creation failed: {error}"
            )
