import shutil
import os
import json
import typer
from datetime import datetime
from pathlib import Path
from typing import Optional

from frappe_manager import CLI_DIR
from frappe_manager.console_manager.Richprint import richprint
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.docker_wrapper.utils import run_command_with_exit_code
from frappe_manager.utils import (
    random_password_generate,
    get_unix_groups,
    check_ports_with_msg,
    host_run_cp,
)
from frappe_manager.docker_wrapper import DockerClient, DockerException


class GlobalServices:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, verbose: bool = False) -> None:
        self.path = CLI_DIR / "services"
        self.quiet = not verbose
        self.typer_context: Optional[typer.Context] = None

    def set_typer_context(self, ctx: typer.Context):
        """
        The function sets the typer context from the
        :param typer context
        :type ctx: typer.Context
        """
        self.typer_context = ctx

    def init(self):
        # check if the global services exits if not then create
        compose_path = self.path / "docker-compose.yml"
        self.composefile = ComposeFile(
            compose_path, template_name="docker-compose.services.tmpl"
        )
        self.docker = DockerClient(compose_file_path=self.composefile.compose_path)

        if not self.docker.server_running():
            richprint.exit("Docker daemon not running. Please start docker service.")

        if not self.path.exists():
            self.path.mkdir(parents=True, exist_ok=True)
            self.create()
            self.start()

        if not compose_path.exists():
            richprint.exit(
                "Seems like global services has taken a down. Please recreate global services."
            )

        if not self.typer_context.invoked_subcommand == "service":
            if not self.running():
                richprint.warning("Global services are not running. Starting it")
                self.start()

    def create(self, backup=False):
        envs = {
            "global-db": {
                "MYSQL_ROOT_PASSWORD_FILE": '/run/secrets/db_root_password',
                "MYSQL_DATABASE": "root",
                "MYSQL_USER": "admin",
                "MYSQL_PASSWORD_FILE": '/run/secrets/db_password',
            }
        }

        user = {
            "global-db": {
                "uid": os.getuid(),
                "gid": os.getuid(),
            },
            "global-nginx-proxy": {
                "uid": os.getuid(),
                "gid": get_unix_groups()["docker"],
            },
        }

        inputs = {"environment": envs, "user": user}

        if backup:
            if self.path.exists():
                backup = CLI_DIR / "backups"
                backup.mkdir(parents=True, exist_ok=True)
                current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                backup_dir_name = f"services_{current_time}"
                self.path.rename(backup / backup_dir_name)

        shutil.rmtree(self.path)

        # create required directories
        # this list of directores can be automated
        dirs_to_create = [
            "mariadb/data",
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
            "secrets"
        ]

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

        with open(db_password_path,'w') as f:
            f.write(random_password_generate(password_length=16,symbols=True))

        with open(db_root_password_path,'w') as f:
            f.write(random_password_generate(password_length=24,symbols=True))


        # populate mariadb config
        mariadb_conf = self.path / "mariadb/conf"
        mariadb_conf = str(mariadb_conf.absolute())
        host_run_cp(
            image="mariadb:10.6",
            source="/etc/mysql",
            destination=mariadb_conf,
            docker=self.docker,
        )
        # set secrets in compose
        self.generate_compose(inputs)
        self.composefile.set_secret_file_path('db_password',str(db_password_path.absolute()))
        self.composefile.set_secret_file_path('db_root_password',str(db_root_password_path.absolute()))
        self.composefile.write_to_file()

    def get_database_info(self):
        """
        Provides info about databse
        """
        info: dict = {}
        try:
            password_path = self.composefile.get_secret_file_path('db_root_password')
            with open(Path(password_path),'r') as f:
                password = f.read()
                info["password"] = password
                info["user"] = "root"
                info["host"] = "global-db"
                info["port"] = 3306
            return info
        except KeyError as e:
            return None

    def exists(self):
        return (self.path / "docker-compose.yml").exists()

    def generate_compose(self, inputs: dict):
        """
        This can get a file like
        inputs = {
        "environment" : {'key': 'value'},
        "extrahosts" : {'key': 'value'},
        "user" : {'uid': 'value','gid': 'value'},
        "labels" : {'key': 'value'},
        }
        """
        try:
            # handle envrionment
            if "environment" in inputs.keys():
                environments: dict = inputs["environment"]
                self.composefile.set_all_envs(environments)

            # handle lablels
            if "labels" in inputs.keys():
                labels: dict = inputs["labels"]
                self.composefile.set_all_labels(labels)

            # handle user
            if "user" in inputs.keys():
                user: dict = inputs["user"]
                for container_name in user.keys():
                    uid = user[container_name]["uid"]
                    gid = user[container_name]["gid"]
                    self.composefile.set_user(container_name, uid, gid)

        except Exception as e:
            richprint.exit(f"Not able to generate global site compose. Error: {e}")

    def get_services_running_status(self) -> dict:
        services = self.composefile.get_services_list()
        containers = self.composefile.get_container_names().values()
        services_status = {}
        try:
            output = self.docker.compose.ps(
                service=services, format="json", all=True, stream=True
            )
            status: dict = {}
            for source, line in output:
                if source == "stdout":
                    status = json.loads(line.decode())

            # this is done to exclude docker runs using docker compose run command
            for container in status:
                if container["Name"] in containers:
                    services_status[container["Service"]] = container["State"]
            return services_status
        except DockerException as e:
            richprint.exit(f"{e.stdout}{e.stderr}")

    def is_service_running(self, service):
        running_status = self.get_services_running_status()
        if running_status[service] == "running":
            return True
        else:
            return False

    def running(self) -> bool:
        """
        The `running` function checks if all the services defined in a Docker Compose file are running.
        :return: a boolean value. If the number of running containers is greater than or equal to the number
        of services listed in the compose file, it returns True. Otherwise, it returns False.
        """
        services = self.composefile.get_services_list()
        running_status = self.get_services_running_status()

        if running_status:
            for service in services:
                if not running_status[service] == "running":
                    return False
        else:
            return False
        return True

    def get_host_port_binds(self):
        try:
            output = self.docker.compose.ps(all=True, format="json", stream=True)
            status: dict = {}
            for source, line in output:
                if source == "stdout":
                    status = json.loads(line.decode())
                    break
            ports_info = []
            for container in status:
                try:
                    port_info = container["Publishers"]
                    if port_info:
                        ports_info = ports_info + port_info
                except KeyError as e:
                    pass

            published_ports = set()
            for port in ports_info:
                try:
                    published_port = port["PublishedPort"]
                    if published_port > 0:
                        published_ports.add(published_port)
                except KeyError as e:
                    pass

            return list(published_ports)

        except DockerException as e:
            return []
            # richprint.exit(f"{e.stdout}{e.stderr}")

    def start(self, service=None):
        status_text = "Starting services"
        if not self.running():
            check_ports_with_msg([80, 443], exclude=self.get_host_port_binds())

        if service:
            if self.is_service_running(service):
                richprint.exit(f"{service} is already in running state.")

        try:
            if service:
                output = self.docker.compose.up(
                    services=[service], detach=True, pull="never", stream=self.quiet
                )
            else:
                output = self.docker.compose.up(
                    detach=True, pull="never", stream=self.quiet
                )

            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.exit(f"{status_text}: Failed", error_msg=e)

    def restart(self, service=None):
        status_text = f"Restarting service {service}"
        richprint.change_head(status_text)
        try:
            if service:
                output = self.docker.compose.restart(
                    services=[service], stream=self.quiet
                )
            else:
                output = self.docker.compose.restart(stream=self.quiet)

            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.exit(f"{status_text}: Failed", error_msg=e)

    def stop(self, service=None):
        status_text = "Stopping global services"
        richprint.change_head(status_text)
        try:
            if service:
                status_text = f"Stopping global service {service}"
                output = self.docker.compose.stop(
                    [service], timeout=10, stream=self.quiet
                )
            else:
                output = self.docker.compose.stop(timeout=10, stream=self.quiet)

            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.exit(f"{status_text}: Failed", error_msg=e)

    def shell(self, container: str, user: str | None = None):
        """
        The `shell` function spawns a shell for a specified container and user.

        :param container: The `container` parameter is a string that specifies the name of the container in
        which the shell command will be executed
        :type container: str
        :param user: The `user` parameter is an optional argument that specifies the user under which the
        shell command should be executed. If a user is provided, the shell command will be executed as that
        user. If no user is provided, the shell command will be executed as the default user
        :type user: str | None
        """
        # TODO check user exists
        richprint.stop()
        shell_path = "/bin/bash"
        try:
            if user:
                self.docker.compose.exec(container, user=user, command=shell_path)
            else:
                self.docker.compose.exec(container, command=shell_path)
        except DockerException as e:
            richprint.warning(f"Shell exited with error code: {e.return_code}")
