import shutil
import platform
import os
import json
import typer
from datetime import datetime
from pathlib import Path
from typing import Optional

from frappe_manager import CLI_DIR
from frappe_manager.services_manager.services_exceptions import ServicesComposeNotExist, ServicesDBNotStart, ServicesException, ServicesNotCreated
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.utils.helpers import (
    random_password_generate,
    check_and_display_port_status,
    get_unix_groups,
    # check_ports_with_msg,

)
from frappe_manager.utils.docker import host_run_cp
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.docker_wrapper.DockerException import DockerException


class ServicesManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, verbose: bool = False) -> None:
        self.path = CLI_DIR / "services"
        self.quiet = not verbose
        self.typer_context: Optional[typer.Context] = None
        self.compose_path = self.path / "docker-compose.yml"

    def set_typer_context(self, ctx: typer.Context):
        """
        The function sets the typer context from the
        :param typer context
        :type ctx: typer.Context
        """
        self.typer_context = ctx

    def entrypoint_checks(self, start = False):

        if not self.path.exists():
            try:
                richprint.print(f"Creating services",emoji_code=":construction:")
                self.path.mkdir(parents=True, exist_ok=True)
                self.create()
            except Exception as e:
                raise ServicesNotCreated(f'Error Caused: {e}')

            self.pull()
            richprint.print(f"Creating services: Done")
            if start:
                self.start()

        if not self.compose_path.exists():
            raise ServicesComposeNotExist(f"Seems like services has taken a down. Compose file not found at -> {self.compose_path}. Please recreate services.")

        if start:
            if not self.typer_context.invoked_subcommand == "service":
                if not self.running():
                    richprint.warning("services are not running. Starting it")
                    self.start()

    def init(self):
        # check if the global services exits if not then create

        current_system = platform.system()

        self.composefile = ComposeFile(
            self.compose_path, template_name="docker-compose.services.tmpl"
        )
        if current_system == "Darwin":
            self.composefile = ComposeFile(
                self.compose_path, template_name="docker-compose.services.osx.tmpl"
            )

        self.docker = DockerClient(compose_file_path=self.composefile.compose_path)

    def create(self, backup=False):
        envs = {
            "global-db": {
                "MYSQL_ROOT_PASSWORD_FILE": '/run/secrets/db_root_password',
                "MYSQL_DATABASE": "root",
                "MYSQL_USER": "admin",
                "MYSQL_PASSWORD_FILE": '/run/secrets/db_password',
            }
        }

        current_system = platform.system()

        inputs = {"environment": envs}

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

            inputs["user"]= user

        except KeyError:
            raise ServicesException("docker group not found in system. Please add docker group to the system and current user to the docker group.")


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
            #"mariadb/data",

        # set secrets in compose
        self.generate_compose(inputs)

        if current_system == "Darwin":
            self.composefile.remove_container_user('global-nginx-proxy')
            self.composefile.remove_container_user('global-db')
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

        with open(db_password_path,'w') as f:
            f.write(random_password_generate(password_length=16,symbols=True))

        with open(db_root_password_path,'w') as f:
            f.write(random_password_generate(password_length=24,symbols=True))


        # populate mariadb config
        mariadb_conf = self.path / "mariadb/conf"
        mariadb_conf = str(mariadb_conf.absolute())
        host_run_cp(
            image="mariadb:10.6",
            source="/etc/mysql/.",
            destination=mariadb_conf,
            docker=self.docker,
        )

        self.composefile.set_secret_file_path('db_password',str(db_password_path.absolute()))
        self.composefile.set_secret_file_path('db_root_password',str(db_root_password_path.absolute()))
        self.composefile.write_to_file()

    def get_database_info(self):
        """
        Provides info about databse
        """
        info: dict = {}
        info["user"] = "root"
        info["host"] = "global-db"
        info["port"] = 3306
        try:
            password_path = self.composefile.get_secret_file_path('db_root_password')
            with open(Path(password_path),'r') as f:
                password = f.read()
                info["password"] = password
            return info
        except KeyError as e:
            # TODO secrets not exists
            info["password"] = None
            return info

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
            # handle environment
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

    def pull(self):
        """
        The function pulls Docker images and displays the status of the operation.
        """
        status_text = "Pulling services images"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.pull(stream=self.quiet)
            richprint.stdout.clear_live()
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.warning(f"{status_text}: Failed")

    def get_services_running_status(self) -> dict:
        services = self.composefile.get_services_list()
        containers = self.composefile.get_container_names().values()
        services_status = {}
        try:
            output = self.docker.compose.ps(
                service=services, format="json", all=True, stream=True
            )
            status: list = []
            for source, line in output:
                if source == "stdout":
                    current_status = json.loads(line.decode())
                    if type(current_status) == dict:
                        status.append(current_status)
                    else:
                        status += current_status

            # this is done to exclude docker runs using docker compose run command
            for container in status:
                if container["Name"] in containers:
                    services_status[container["Service"]] = container["State"]
            return services_status
        except DockerException as e:
            richprint.exit(f"{e.stdout}{e.stderr}")

    def is_service_running(self, service):
        running_status = self.get_services_running_status()
        try:
            if running_status[service] == "running":
                return True
            else:
                return False
        except KeyError:
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
                try:
                    if not running_status[service] == "running":
                        return False
                except KeyError:
                    return False
        else:
            return False
        return True

    def get_host_port_binds(self):
        try:
            output = self.docker.compose.ps(all=True, format="json", stream=True)
            status_list: list = []

            for source, line in output:
                if source == "stdout":
                    status = json.loads(line.decode())
                    if type(status) == list:
                        status_list += status
                    else:
                        status_list.append(status)

            ports_info = []

            for container in status_list:
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

        except Exception as e:
            return []
            # richprint.exit(f"{e.stdout}{e.stderr}")

    def start(self, service=None):
        if not self.running():
            docker_used_ports = self.get_host_port_binds()
            check_and_display_port_status([80, 443], exclude=docker_used_ports)

        status_text = "Starting services"
        richprint.change_head(status_text)

        if service:
            if self.is_service_running(service):
                richprint.warning(f"{service} is already in running state.")

        try:
            if service:
                output = self.docker.compose.up(
                    services=[service], detach=True, pull="missing", stream=self.quiet
                )
            else:
                output = self.docker.compose.up(
                    detach=True, pull="missing", stream=self.quiet
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
                    [service], stream=self.quiet
                )
            else:
                output = self.docker.compose.stop(stream=self.quiet)

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

    def remove_db_user(self,user_name):
        global_db_info = self.get_database_info()
        db_user = global_db_info["user"]
        db_password = global_db_info["password"]

        remove_db_user = f"/usr/bin/mariadb -u{db_user} -p'{db_password}' -e 'DROP USER `{user_name}`@`%`;'"
        # show_db_user= f"/usr/bin/mariadb -h{global_db_info['host']} -u{global_db_info['user']} -p'{global_db_info['password']}' -e 'SELECT User, Host FROM mysql.user;'"
        # output = self.docker.compose.exec('frappe',command=show_db_user)
        try:
            output = self.docker.compose.exec(
                "global-db", command=remove_db_user, stream=self.quiet
            )
            if self.quiet:
                exit_code = richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"Removed {user_name} DB User: Done")
        except DockerException as e:
            richprint.warning(f"Remove DB User: Failed")

    def remove_db(self,db_name):

        global_db_info = self.get_database_info()
        db_user = global_db_info["user"]
        db_password = global_db_info["password"]

        # remove database
        remove_db_command = f"/usr/bin/mariadb -u{db_user} -p'{db_password}' -e 'DROP DATABASE `{db_name}`;'"
        # show_db_command = f"/usr/bin/mariadb -h{global_db_info['host']} -u{global_db_info['user']} -p'{global_db_info['password']}' -e 'show databases;'"
        # output = self.docker.compose.exec('frappe',command=show_db_command)
        try:
            output = self.docker.compose.exec(
                "global-db", command=remove_db_command, stream=self.quiet
            )
            if self.quiet:
                exit_code = richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"Removed {db_name} DB: Done")
        except DockerException as e:
            richprint.warning(f"Remove DB: Failed")

    def down(self, remove_ophans=True, volumes=True) -> bool:
        """
        The `down` function removes containers using Docker Compose and prints the status of the operation.
        """
        if self.composefile.exists():
            status_text = "Removing Containers"
            richprint.change_head(status_text)
            try:
                output = self.docker.compose.down(
                    remove_orphans=remove_ophans,
                    volumes=volumes,
                    stream=self.quiet,
                )
                if self.quiet:
                    exit_code = richprint.live_lines(output, padding=(0, 0, 0, 2))
                richprint.print(f"Removing Containers: Done")
            except DockerException as e:
                richprint.exit(f"{status_text}: Failed")

    def add_user(self, site_db_name, site_db_user, site_db_pass, timeout = 25):

        db_host = '127.0.0.1'
        global_db_info = self.get_database_info()
        db_user = global_db_info["user"]
        db_password = global_db_info["password"]


        remove_db_user = f"/usr/bin/mariadb -P3306 -h{db_host} -u{db_user} -p'{db_password}' -e 'DROP USER `{site_db_user}`@`%`;'"
        add_db_user = f"/usr/bin/mariadb -h{db_host} -P3306 -u{db_user} -p'{db_password}' -e 'CREATE USER `{site_db_user}`@`%` IDENTIFIED BY \"{site_db_pass}\";'"
        grant_user = f"/usr/bin/mariadb -h{db_host} -P3306 -u{db_user} -p'{db_password}' -e 'GRANT ALL PRIVILEGES ON `{site_db_name}`.* TO `{site_db_user}`@`%`;'"

        removed = True

        try:
            output = self.docker.compose.exec('global-db', command=remove_db_user, stream=self.quiet,stream_only_exit_code=True)
        except DockerException as e:
            removed = False
            if 'error 1396' in str(e.stderr).lower():
                removed = True

        if removed:
            try:
                output = self.docker.compose.exec("global-db", command=add_db_user, stream=self.quiet,stream_only_exit_code=True)
                output = self.docker.compose.exec("global-db", command=grant_user, stream=self.quiet,stream_only_exit_code=True)
                richprint.print(f"Recreated user {site_db_user}")
            except DockerException as e:
                raise ServicesDBNotStart(f"Database user creation failed: {e}")

    def remove_itself(self):
        shutil.rmtree(self.path)
