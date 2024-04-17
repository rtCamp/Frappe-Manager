import shutil
import platform
import os
import typer
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from frappe_manager import CLI_DIR, CLI_SERVICES_DIRECTORY
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo, DatabaseServiceManager, MariaDBManager
from frappe_manager.services_manager.services_exceptions import ServicesComposeNotExist, ServicesException, ServicesNotCreated
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.utils.helpers import (
    random_password_generate,
    check_and_display_port_status,
    get_unix_groups,
)
from frappe_manager.utils.docker import host_run_cp
from frappe_manager.docker_wrapper.DockerException import DockerException


class ServicesManager:
    def __init__(self, path = CLI_SERVICES_DIRECTORY, verbose: bool = False,) -> None:
        self.path = path
        self.quiet = not verbose
        self.compose_path = self.path / "docker-compose.yml"
        self.typer_context: Optional[typer.Context] = None

    def entrypoint_checks(self, start = False):

        if not self.path.exists():
            try:
                richprint.print(f"Creating services",emoji_code=":construction:")
                self.path.mkdir(parents=True, exist_ok=True)
                self.create()
            except Exception as e:
                raise ServicesNotCreated(f'Error Caused: {e}')

            self.compose_project.pull_images()

            richprint.print(f"Creating services: Done")
            if start:
                self.compose_project.start_service()

        if not self.compose_path.exists():
            raise ServicesComposeNotExist(f"Seems like services has taken a down. Compose file not found at -> {self.compose_path}. Please recreate services.")

        if start:
            if not self.typer_context.invoked_subcommand == "service":
                if not self.compose_project.running:
                    richprint.warning("services are not running. Starting it")
                    self.compose_project.start_service()

        self.database_manager: DatabaseServiceManager = MariaDBManager(DatabaseServerServiceInfo.import_from_compose_file('global-db',self.compose_project),self.compose_project)

    def init(self):
        # check if the global services exits if not then create
        # TODO this should be done by factory
        current_system = platform.system()

        compose_file_manager = ComposeFile(
            self.compose_path, template_name="docker-compose.services.tmpl"
        )

        if current_system == "Darwin":
            compose_file_manager = ComposeFile(
                self.compose_path, template_name="docker-compose.services.osx.tmpl"
            )

        self.compose_project= ComposeProject(compose_file_manager=compose_file_manager)

    def set_typer_context(self, ctx: typer.Context):
        """
        The function sets the typer context
        """
        self.typer_context = ctx


    def create(self, backup: bool = False,clean_install: bool = True):
        envs = {
            "global-db": {
                "MYSQL_ROOT_PASSWORD_FILE": '/run/secrets/db_root_password',
                "MYSQL_DATABASE": "root",
                "MYSQL_USER": "admin",
                "MYSQL_PASSWORD_FILE": '/run/secrets/db_password',
            }
        }
        current_system = platform.system()
        inputs:  dict[str, Any] = {"environment": envs}
        try:
            user = {
                "global-db": {
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                }}

            if not current_system == "Darwin":

                user["global-nginx-proxy"] = {
                    "uid": os.getuid(),
                    "gid": get_unix_groups()["docker"],
                }

            inputs["user"] = user
        except KeyError:
            raise ServicesException("docker group not found in system. Please add docker group to the system and current user to the docker group.")

        if backup:
            if self.path.exists():
                backup_path: Path = CLI_DIR / "backups"
                backup_path.mkdir(parents=True, exist_ok=True)
                current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                backup_dir_name = f"services_{current_time}"
                self.path.rename(backup_path / backup_dir_name)

        shutil.rmtree(self.path)

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
            "nginx-proxy/ssl",
            "nginx-proxy/cache",
            "secrets"
        ]

        # set secrets in compose
        self.generate_compose(inputs)

        if current_system == "Darwin":
            self.compose_project.compose_file_manager.remove_container_user('global-nginx-proxy')
            self.compose_project.compose_file_manager.remove_container_user('global-db')
        else:
            dirs_to_create.append("mariadb/data")

        # create dirs
        for folder in dirs_to_create:
            temp_dir = self.path / folder
            try:
                temp_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                richprint.exit(
                    f"Failed to create global services bind mount directories. Error: {e}"
                )

        # populate secrets for db
        db_password_path = self.path/ 'secrets'/ 'db_password.txt'
        db_root_password_path = self.path/ 'secrets'/ 'db_root_password.txt'

        db_password_path.write_text(random_password_generate(password_length=16,symbols=True))
        db_root_password_path.write_text(random_password_generate(password_length=24,symbols=True))

        # populate mariadb config
        mariadb_conf = self.path / "mariadb/conf"
        mariadb_conf = str(mariadb_conf.absolute())
        host_run_cp(
            image="mariadb:10.6",
            source="/etc/mysql/.",
            destination=mariadb_conf,
            docker=self.compose_project.docker,
        )

        self.compose_project.compose_file_manager.set_secret_file_path('db_password',str(db_password_path.absolute()))
        self.compose_project.compose_file_manager.set_secret_file_path('db_root_password',str(db_root_password_path.absolute()))
        self.compose_project.compose_file_manager.write_to_file()

        if clean_install:
            # remove previous contaniners and volumes
            self.compose_project.docker.compose.down(remove_orphans=True,timeout=1,volumes=True,stream=True)

    def exists(self):
        return (self.path / "docker-compose.yml").exists()

    def generate_compose(self, inputs: dict):
        # TODO do something about this function
        try:
            # handle environment
            if "environment" in inputs.keys():
                environments: dict = inputs["environment"]
                self.compose_project.compose_file_manager.set_all_envs(environments)

            # handle lablels
            if "labels" in inputs.keys():
                labels: dict = inputs["labels"]
                self.compose_project.compose_file_manager.set_all_labels(labels)

            # handle user
            if "user" in inputs.keys():
                user: dict = inputs["user"]
                for container_name in user.keys():
                    uid = user[container_name]["uid"]
                    gid = user[container_name]["gid"]
                    self.compose_project.compose_file_manager.set_user(container_name, uid, gid)

        # TODO do something about this exception
        except Exception as e:
            richprint.exit(f"Not able to generate global site compose. Error: {e}")


    def shell(self, container: str, user: str | None = None):
        richprint.stop()
        shell_path = "/bin/bash"
        try:
            if user:
                self.compose_project.docker.compose.exec(container, user=user, command=shell_path,capture_output=False)
            else:
                self.compose_project.docker.compose.exec(container, command=shell_path,capture_output=False)
        except DockerException as e:
            richprint.warning(f"Shell exited with error code: {e.return_code}")

    def remove_itself(self):
        shutil.rmtree(self.path)

    def are_ports_free(self):
        docker_used_ports = self.compose_project.get_host_port_binds()
        check_and_display_port_status([80, 443], exclude=docker_used_ports)
