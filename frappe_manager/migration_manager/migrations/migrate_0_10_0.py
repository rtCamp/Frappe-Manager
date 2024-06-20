import os
import platform
import shutil
from datetime import datetime
from copy import deepcopy
from pathlib import Path
from typing import Any
from frappe_manager import CLI_DIR
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.migration_manager.backup_manager import BackupData
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_exections import MigrationExceptionInBench
from frappe_manager.migration_manager.migration_helpers import (
    MigrationBench,
    MigrationBenches,
    MigrationServicesManager,
)
from frappe_manager.services_manager.database_service_manager import (
    DatabaseServerServiceInfo,
    DatabaseServiceManager,
    MariaDBManager,
)
from frappe_manager.services_manager.services_exceptions import (
    DatabaseServiceException,
    ServicesException,
    ServicesNotCreated,
)
from frappe_manager.site_manager.site_exceptions import BenchDockerComposeFileNotFound
from frappe_manager.utils.docker import host_run_cp
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import get_container_name_prefix, get_unix_groups, random_password_generate
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.migration_manager.version import Version


class MigrationV0100(MigrationBase):
    version = Version("0.10.0")

    def init(self):
        self.benches_dir = CLI_DIR / "sites"
        self.backup_manager = BackupManager(name=str(self.version), benches_dir=self.benches_dir)
        self.string_timestamp = datetime.now().strftime("%d-%b-%y--%H-%M-%S")
        self.benches_manager = MigrationBenches(self.benches_dir)
        self.services_manager: MigrationServicesManager = MigrationServicesManager()

    def get_rollback_version(self):
        # this was without any migrations
        return Version("0.10.1")

    def up(self):
        richprint.stdout.rule(f':package: [bold][blue]v{str(self.version)}[/blue][bold]')
        self.logger.info(f"v{str(self.version)}: Started")
        self.logger.info("-" * 40)

        self.init()
        self.migrate_services()
        self.migrate_benches()

        self.logger.info("-" * 40)

    def migrate_services(self):
        # create services
        self.services_create(self.services_manager.compose_project)
        self.services_manager.compose_project.pull_images()
        self.services_manager.compose_project.start_service(force_recreate=True)

        self.services_database_manager: DatabaseServiceManager = MariaDBManager(
            DatabaseServerServiceInfo.import_from_compose_file('global-db', self.services_manager.compose_project),
            self.services_manager.compose_project,
        )

    def migrate_benches(self):
        main_error = False

        # migrate each bench
        for bench_name, bench_path in self.benches_manager.get_all_benches().items():
            bench = MigrationBench(name=bench_name, path=bench_path.parent)

            if bench.name in self.migration_executor.migrate_benches.keys():
                bench_info = self.migration_executor.migrate_benches[bench.name]
                if bench_info['exception']:
                    richprint.print(f"Skipping migration for failed bench {bench.name}.")
                    main_error = True
                    continue

            self.migration_executor.set_bench_data(bench, migration_version=self.version)
            try:
                self.migrate_bench(bench)
            except Exception as e:
                import traceback

                traceback_str = traceback.format_exc()
                self.logger.error(f"{bench.name} [ EXCEPTION TRACEBACK ]:\n {traceback_str}")
                richprint.update_live()
                main_error = True
                self.migration_executor.set_bench_data(bench, e, self.version)

                # restore all backup files
                for backup in self.backup_manager.backups:
                    if backup.bench == bench.name:
                        self.backup_manager.restore(backup, force=True)

                self.undo_bench_migrate(bench)
                self.logger.info(f'Undo successfull for bench: {bench.name}')
                bench.compose_project.down_service(volumes=False, timeout=5)

        if main_error:
            raise MigrationExceptionInBench('')

    def migrate_bench(self, bench: MigrationBench):
        richprint.change_head("Migrating bench compose")

        bench.compose_project.down_service(volumes=False)

        self.migrate_bench_compose(bench)

        bench.compose_project.down_service(volumes=False)

        bench_db_info = bench.get_db_connection_info()
        bench_db_name = bench_db_info["name"]
        bench_db_user = bench_db_info["user"]
        bench_db_pass = bench_db_info["password"]

        self.services_database_manager.add_user(bench_db_user, bench_db_pass, force=True)
        self.services_database_manager.grant_user_privilages(bench_db_name, bench_db_user)

    def undo_services_migrate(self):
        self.services_manager.compose_project.down_service()
        if self.services_manager.services_path.exists():
            shutil.rmtree(self.services_manager.services_path)

    def undo_bench_migrate(self, bench: MigrationBench):
        configs_backup = bench.path / f"configs-{self.string_timestamp}.bak"
        configs_path = bench.path / "configs"

        if configs_path.exists():
            shutil.rmtree(configs_path)

        if configs_backup.exists():
            shutil.copytree(configs_backup, configs_path)
            shutil.rmtree(configs_backup)
            self.logger.info(f"Removed : {configs_backup}")

        # start each bench forcefully and add user to the bench
        try:
            bench.compose_project.start_service(['mariadb'])
            bench_db_info = DatabaseServerServiceInfo.import_from_compose_file('mariadb', bench.compose_project)
            bench_db_manager = MariaDBManager(bench_db_info, bench.compose_project)
            bench_db_info = bench.get_db_connection_info()
            bench_db_name = bench_db_info["name"]
            bench_db_user = bench_db_info["user"]
            bench_db_pass = bench_db_info["password"]
            try:
                bench_db_manager.add_user(db_user=bench_db_user, db_pass=bench_db_pass, force=True)
                bench_db_manager.grant_user_privilages(bench_db_user, bench_db_name)
            except DatabaseServiceException as e:
                pass
            bench.compose_project.down_service(volumes=False)
        except DockerException as e:
            pass
        self.logger.info(f"Undo successfull for bench: {bench.name}")

    def migrate_bench_compose(self, bench: MigrationBench):
        richprint.change_head("Migrating database")

        if not bench.compose_project.compose_file_manager.exists():
            raise BenchDockerComposeFileNotFound(bench.name, bench.compose_project.compose_file_manager.compose_path)

        db_backup_file = self.db_migration_export(bench)

        richprint.print(f"[blue]{bench.name}[/blue] db exported from bench mariadb service.")

        # backup bench db
        db_backup = self.backup_manager.backup(db_backup_file, bench_name=bench.name, allow_restore=False)

        self.db_migration_import(bench=bench, db_backup_file=db_backup)

        richprint.print(f"[blue]{bench.name}[/blue] db imported to global-db service.")

        richprint.change_head("Migrating bench compose")

        # get all the payloads
        envs = bench.compose_project.compose_file_manager.get_all_envs()
        labels = bench.compose_project.compose_file_manager.get_all_labels()

        # introduced in v0.10.0
        if not "ENVIRONMENT" in envs["frappe"]:
            envs["frappe"]["ENVIRONMENT"] = "dev"

        envs["frappe"]["CONTAINER_NAME_PREFIX"] = get_container_name_prefix(bench.name)
        envs["frappe"]["MARIADB_ROOT_PASS"] = "root"
        envs["frappe"]["MARIADB_HOST"] = "global-db"

        envs["nginx"]["VIRTUAL_HOST"] = bench.name

        envs["adminer"] = {"ADMINER_DEFAULT_SERVER": "global-db"}

        envs_user_info = {}
        userid_groupid: dict = {"USERID": os.getuid(), "USERGROUP": os.getgid()}

        env_user_info_container_list = ["frappe", "schedule", "socketio"]

        for env in env_user_info_container_list:
            envs_user_info[env] = deepcopy(userid_groupid)

        # overwrite user for each invocation
        users = {"nginx": {"uid": os.getuid(), "gid": os.getgid()}}

        self.create_compose_dirs(bench)

        bench.compose_project.compose_file_manager.template_name = "docker-compose.tmpl"

        # load template
        bench.compose_project.compose_file_manager.yml = bench.compose_project.compose_file_manager.load_template()

        # set all the payload
        bench.compose_project.compose_file_manager.set_all_envs(envs)
        bench.compose_project.compose_file_manager.set_all_envs(envs_user_info)
        bench.compose_project.compose_file_manager.set_all_labels(labels)
        bench.compose_project.compose_file_manager.set_all_users(users)

        bench.compose_project.compose_file_manager.remove_secrets_from_container("frappe")
        bench.compose_project.compose_file_manager.remove_root_secrets_compose()
        bench.compose_project.compose_file_manager.set_network_alias("nginx", "site-network", [bench.name])
        bench.compose_project.compose_file_manager.set_container_names(get_container_name_prefix(bench.name))

        bench.compose_project.compose_file_manager.set_version(str(self.version))
        bench.compose_project.compose_file_manager.set_top_networks_name(
            "site-network", get_container_name_prefix(bench.name)
        )
        bench.compose_project.compose_file_manager.write_to_file()

        # change the node socketio port
        bench.common_bench_config_set({"socketio_port": "80"})

        richprint.print(f"Migrated [blue]{bench.name}[/blue] compose file.")

        return db_backup

    def create_compose_dirs(self, bench: MigrationBench):
        richprint.change_head("Creating services compose dirs.")

        # directory creation
        configs_path = bench.path / "configs"

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

        nginx_image = bench.compose_project.compose_file_manager.yml["services"]["nginx"]["image"]

        for directory in nginx_poluate_dir:
            new_dir = nginx_dir / directory
            if not new_dir.exists():
                new_dir_abs = str(new_dir.absolute())
                host_run_cp(
                    nginx_image,
                    source="/etc/nginx",
                    destination=new_dir_abs,
                    docker=bench.compose_project.docker,
                )

        nginx_subdirs = ["logs", "cache", "run"]

        for directory in nginx_subdirs:
            new_dir = nginx_dir / directory
            new_dir.mkdir(parents=True, exist_ok=True)

        richprint.print("Created services compose dirs.")

    def db_migration_export(self, bench: MigrationBench) -> Path:
        self.logger.debug("[db export] bench: %s", bench.name)

        # start the benc hand also handle the missing docker images
        output = bench.compose_project.docker.compose.up(
            services=["mariadb", "frappe"], detach=True, pull="missing", stream=False
        )

        self.logger.debug("[db export] checking if mariadb started")

        bench_db_server_info = DatabaseServerServiceInfo.import_from_compose_file('mariadb', bench.compose_project)
        bench_mariadb_manager = MariaDBManager(bench_db_server_info, bench.compose_project)

        # wait till db started
        bench_mariadb_manager.wait_till_db_start()

        # create dir to store migration
        db_migration_dir_path = bench.path / "workspace" / "migrations"
        db_migration_dir_path.mkdir(exist_ok=True)

        current_datetime = datetime.now()
        formatted_date = current_datetime.strftime("%d-%m-%Y--%H-%M-%S")

        db_migration_file_path = f"/workspace/migrations/db-{bench.name}-{formatted_date}.sql"

        bench_db_info = bench.get_db_connection_info()
        bench_db_name = bench_db_info["name"]

        bench_frappe_db_manager = MariaDBManager(bench_db_server_info, bench.compose_project, 'frappe')
        bench_frappe_db_manager.db_export(bench_db_name, db_migration_file_path)

        output_stop = bench.compose_project.docker.compose.stop(timeout=10, stream=False)

        bench_db_migration_file_path = Path(bench.path / db_migration_file_path[1:])

        return bench_db_migration_file_path

    def db_migration_import(self, bench: MigrationBench, db_backup_file: BackupData):
        self.logger.info(f"[database import: global-db] {bench.name} -> {db_backup_file}")

        bench_db_info = bench.get_db_connection_info()
        bench_db_name = bench_db_info["name"]
        bench_db_user = bench_db_info["user"]
        bench_db_pass = bench_db_info["password"]

        # wait till db starts
        self.services_database_manager.wait_till_db_start()

        # import db if db found then remove it and add
        self.services_database_manager.db_import(bench_db_name, db_backup_file.src, True)

        # add user if exits then remove and add
        self.services_database_manager.add_user(bench_db_user, bench_db_pass, force=True)

        self.services_database_manager.grant_user_privilages(bench_db_user, bench_db_name)

        richprint.print(f"{bench.name} db imported.")

    def services_create(self, services_compose_project: ComposeProject):
        richprint.change_head("Creating services.")

        envs = {
            "global-db": {
                "MYSQL_ROOT_PASSWORD_FILE": '/run/secrets/db_root_password',
                "MYSQL_DATABASE": "root",
                "MYSQL_USER": "admin",
                "MYSQL_PASSWORD_FILE": '/run/secrets/db_password',
            }
        }
        current_system = platform.system()
        inputs: dict[str, Any] = {"environment": envs}
        try:
            user = {
                "global-db": {
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                }
            }

            if not current_system == "Darwin":
                user["global-nginx-proxy"] = {
                    "uid": os.getuid(),
                    "gid": get_unix_groups()["docker"],
                }
            inputs["user"] = user

        except KeyError:
            raise ServicesException(
                "docker group not found in system. Please add docker group to the system and current user to the docker group."
            )

        if self.services_manager.services_path.exists():
            shutil.rmtree(self.services_manager.services_path)

        # create required directories
        dirs_to_create = [
            "mariadb/conf",
            "mariadb/logs",
            "nginx-proxy/dhparam",
            "nginx-proxy/certs",
            "nginx-proxy/confd",
            "nginx-proxy/htpasswd",
            "nginx-proxy/vhostd",
            "nginx-proxy/html",
            "nginx-proxy/logs",
            "nginx-proxy/run",
            "nginx-proxy/cache",
            "secrets",
        ]

        # set secrets in compose
        self.generate_compose(inputs, services_compose_project.compose_file_manager)

        if current_system == "Darwin":
            services_compose_project.compose_file_manager.remove_container_user('global-nginx-proxy')
            services_compose_project.compose_file_manager.remove_container_user('global-db')
        else:
            dirs_to_create.append("mariadb/data")

        # create dirs
        for folder in dirs_to_create:
            temp_dir = self.services_manager.services_path / folder
            try:
                temp_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                richprint.exit(f"Failed to create global services bind mount directories. Error: {e}")

        # populate secrets for db
        db_password_path = self.services_manager.services_path / 'secrets' / 'db_password.txt'
        db_root_password_path = self.services_manager.services_path / 'secrets' / 'db_root_password.txt'

        db_password_path.write_text(random_password_generate(password_length=16, symbols=True))
        db_root_password_path.write_text(random_password_generate(password_length=24, symbols=True))

        # populate mariadb config
        mariadb_conf = self.services_manager.services_path / "mariadb/conf"
        mariadb_conf = str(mariadb_conf.absolute())
        host_run_cp(
            image="mariadb:10.6",
            source="/etc/mysql/.",
            destination=mariadb_conf,
            docker=services_compose_project.docker,
        )

        services_compose_project.compose_file_manager.set_secret_file_path(
            'db_password', str(db_password_path.absolute())
        )
        services_compose_project.compose_file_manager.set_secret_file_path(
            'db_root_password', str(db_root_password_path.absolute())
        )
        services_compose_project.compose_file_manager.write_to_file()

        services_compose_project.docker.compose.down(remove_orphans=True, timeout=1, volumes=True, stream=True)

        richprint.print(f"Created services at {self.services_manager.services_path}.")

    def generate_compose(self, inputs: dict, compose_file_manager: ComposeFile):
        richprint.change_head(f"Generating services compose file.")
        try:
            # handle environment
            if "environment" in inputs.keys():
                environments: dict = inputs["environment"]
                compose_file_manager.set_all_envs(environments)

            # handle lablels
            if "labels" in inputs.keys():
                labels: dict = inputs["labels"]
                compose_file_manager.set_all_labels(labels)

            # handle user
            if "user" in inputs.keys():
                user: dict = inputs["user"]
                for container_name in user.keys():
                    uid = user[container_name]["uid"]
                    gid = user[container_name]["gid"]
                    compose_file_manager.set_user(container_name, uid, gid)
            richprint.print(f"Generated services compose file.")
        except Exception:
            raise ServicesNotCreated()
