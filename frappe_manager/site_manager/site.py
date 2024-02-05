from copy import deepcopy
import importlib
import shutil
import re
import json
from typing import List, Type
from pathlib import Path
from rich import inspect

from rich.table import Table
from frappe_manager.docker_wrapper import DockerClient, DockerException

from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.site_manager.site_exceptions import (
    SiteDatabaseAddUserException,
    SiteException,
)
from frappe_manager.site_manager.workers_manager.SiteWorker import SiteWorkers
from frappe_manager.utils.helpers import log_file, get_container_name_prefix
from frappe_manager.utils.docker import host_run_cp


class Site:
    def __init__(self, path: Path, name: str, verbose: bool = False, services=None):
        self.path = path
        self.name = name
        self.quiet = not verbose
        self.services = services
        # self.logger = log.get_logger()
        self.init()

    def init(self):
        """
        The function checks if the Docker daemon is running and exits with an error message if it is not.
        """
        self.composefile = ComposeFile(self.path / "docker-compose.yml")
        self.docker = DockerClient(compose_file_path=self.composefile.compose_path)
        self.workers = SiteWorkers(self.path, self.name, self.quiet)

        # remove this from init
        if self.workers.exists():
            if not self.workers.running():
                if self.running():
                    self.workers.start()

    def exists(self):
        """
        The `exists` function checks if a file or directory exists at the specified path.
        :return: a boolean value. If site path exits then returns `True` else `False`.
        """
        return self.path.exists()

    def validate_sitename(self) -> bool:
        """
        The function `validate_sitename` checks if a given sitename is valid by using a regular expression
        pattern.
        :return: a boolean value. If the sitename is valid, it returns True. If the sitename is not valid,
        it returns False.
        """
        sitename = self.name
        match = re.search(
            r"^[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?.localhost$", sitename
        )
        if not match:
            richprint.exit(
                "The site name must follow a single-level subdomain Fully Qualified Domain Name (FQDN) format of localhost, such as 'subdomain.localhost'."
            )

    def get_frappe_container_hex(self) -> None | str:
        """
        The function `get_frappe_container_hex` searches for a Docker container with the name containing
        "-frappe" and returns its hexadecimal representation if found, otherwise returns None.
        :return: either a hexadecimal string representing the name of the Frappe container, or None if no
        Frappe container is found.
        """
        container_name = self.composefile.get_container_names()
        return container_name["frappe"].encode().hex()

    def migrate_site_compose(self):
        """
        The `migrate_site` function checks the environment version and migrates it if necessary.
        :return: a boolean value,`True` if the site migrated else `False`.
        """
        if self.composefile.exists():
            richprint.change_head("Checking Environment Version")
            compose_version = self.composefile.get_version()
            fm_version = importlib.metadata.version("frappe-manager")
            if not compose_version == fm_version:
                status = False
                if self.composefile.exists():
                    # get all the payloads
                    envs = self.composefile.get_all_envs()
                    labels = self.composefile.get_all_labels()

                    # introduced in v0.10.0
                    if not "ENVIRONMENT" in envs["frappe"]:
                        envs["frappe"]["ENVIRONMENT"] = "dev"

                    envs["frappe"]["CONTAINER_NAME_PREFIX"] = get_container_name_prefix(
                        self.name
                    )
                    envs["frappe"]["MARIADB_ROOT_PASS"] = "root"

                    envs["nginx"]["VIRTUAL_HOST"] = self.name

                    import os

                    envs_user_info = {}
                    userid_groupid: dict = {
                        "USERID": os.getuid(),
                        "USERGROUP": os.getgid(),
                    }

                    env_user_info_container_list = ["frappe", "schedule", "socketio"]

                    for env in env_user_info_container_list:
                        envs_user_info[env] = deepcopy(userid_groupid)

                    # overwrite user for each invocation
                    users = {"nginx": {"uid": os.getuid(), "gid": os.getgid()}}

                    # load template
                    self.composefile.yml = self.composefile.load_template()

                    # set all the payload
                    self.composefile.set_all_envs(envs)
                    self.composefile.set_all_envs(envs_user_info)
                    self.composefile.set_all_labels(labels)
                    self.composefile.set_all_users(users)
                    # self.composefile.set_all_extrahosts(extrahosts)

                    self.create_compose_dirs()
                    self.composefile.set_network_alias(
                        "nginx", "site-network", [self.name]
                    )

                    self.composefile.set_secret_file_path(
                        "db_root_password",
                        self.services.composefile.get_secret_file_path(
                            "db_root_password"
                        ),
                    )

                    self.composefile.set_container_names(
                        get_container_name_prefix(self.name)
                    )
                    fm_version = importlib.metadata.version("frappe-manager")
                    self.composefile.set_version(fm_version)
                    self.composefile.set_top_networks_name(
                        "site-network", get_container_name_prefix(self.name)
                    )
                    self.composefile.write_to_file()

                    # change the node socketio port
                    # self.common_site_config_set('socketio_port','80')
                    status = True

                if status:
                    richprint.print(
                        f"Environment Migration Done: {compose_version} -> {fm_version}"
                    )
                else:
                    richprint.print(
                        f"Environment Migration Failed: {compose_version} -> {fm_version}"
                    )
            else:
                richprint.print("Already Latest Environment Version")

    def common_site_config_set(self, config: dict):
        common_site_config_path = (
            self.path / "workspace/frappe-bench/sites/common_site_config.json"
        )
        if common_site_config_path.exists():
            common_site_config = {}

            with open(common_site_config_path, "r") as f:
                common_site_config = json.load(f)

            try:
                for key, value in config.items():
                    common_site_config[key] = value
                with open(common_site_config_path, "w") as f:
                    json.dump(common_site_config, f)
                return True
            except KeyError as e:
                # log error that not able to change common site config
                return False
        else:
            return False

    def generate_compose(self, inputs: dict) -> None:
        """
        The function `generate_compose` sets environment variables, extra hosts, and version information in
        a compose file and writes it to a file.

        :param inputs: The `inputs` parameter is a dictionary that contains the values which will be used in compose file.
        :type inputs: dict
        """
        try:
            if "environment" in inputs.keys():
                environments: dict = inputs["environment"]
                self.composefile.set_all_envs(environments)

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
            richprint.exit(f"Not able to generate site compose. Error: {e}")

        self.composefile.set_network_alias("nginx", "site-network", [self.name])
        self.composefile.set_container_names(get_container_name_prefix(self.name))
        self.composefile.set_secret_file_path(
            "db_root_password",
            self.services.composefile.get_secret_file_path("db_root_password"),
        )
        fm_version = importlib.metadata.version("frappe-manager")
        self.composefile.set_version(fm_version)
        self.composefile.set_top_networks_name(
            "site-network", get_container_name_prefix(self.name)
        )
        self.composefile.write_to_file()

    def create_site_dir(self):
        # create site dir
        self.path.mkdir(parents=True, exist_ok=True)

    def sync_site_common_site_config(self):
        global_db_info = self.services.get_database_info()
        global_db_host = global_db_info["host"]
        global_db_port = global_db_info["port"]

        # set common site config
        common_site_config_data = {
            "socketio_port": "80",
            "db_host": global_db_host,
            "db_port": global_db_port,
            "redis_cache": f"redis://{get_container_name_prefix(self.name)}-redis-cache:6379",
            "redis_queue": f"redis://{get_container_name_prefix(self.name)}-redis-queue:6379",
            "redis_socketio": f"redis://{get_container_name_prefix(self.name)}-redis-cache:6379",
        }
        self.common_site_config_set(common_site_config_data)

    def create_compose_dirs(self) -> bool:
        """
        The function `create_dirs` creates two directories, `workspace` and `certs`, within a specified
        path.
        """
        richprint.change_head("Creating Compose directories")

        # create compose bind dirs -> workspace
        workspace_path = self.path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        configs_path = self.path / "configs"
        configs_path.mkdir(parents=True, exist_ok=True)

        # create nginx dirs
        nginx_dir = configs_path / "nginx"
        nginx_dir.mkdir(parents=True, exist_ok=True)

        nginx_poluate_dir = ["conf"]
        nginx_image = self.composefile.yml["services"]["nginx"]["image"]

        for directory in nginx_poluate_dir:
            new_dir = nginx_dir / directory
            if not new_dir.exists():
                new_dir_abs = str(new_dir.absolute())
                host_run_cp(
                    nginx_image,
                    source="/etc/nginx",
                    destination=new_dir_abs,
                    docker=self.docker,
                )

        nginx_subdirs = ["logs", "cache", "run"]
        for directory in nginx_subdirs:
            new_dir = nginx_dir / directory
            new_dir.mkdir(parents=True, exist_ok=True)

        richprint.print("Creating Compose directories: Done")

    def start(self) -> bool:
        """
        The function starts Docker containers and prints the status of the operation.
        """
        status_text = "Starting Docker Containers"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.up(
                detach=True, pull="never", stream=self.quiet
            )
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.exit(f"{status_text}: Failed", error_msg=e)

        # start workers if exits
        if self.workers.exists():
            self.workers.start()

    def pull(self):
        """
        The function pulls Docker images and displays the status of the operation.
        """
        status_text = "Pulling Docker Images"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.pull(stream=self.quiet)
            richprint.stdout.clear_live()
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.warning(f"{status_text}: Failed")

    def logs(self, service: str, follow: bool = False):
        """
        The `logs` function prints the logs of a specified service, with the option to follow the logs in
        real-time.

        :param service: The "service" parameter is a string that specifies the name of the service whose
        logs you want to retrieve. It is used to filter the logs and only retrieve the logs for that
        specific service
        :type service: str
        :param follow: The `follow` parameter is a boolean flag that determines whether to continuously
        stream the logs or not. If `follow` is set to `True`, the logs will be streamed continuously as they
        are generated. If `follow` is set to `False`, only the existing logs will be returned, defaults to
        False
        :type follow: bool (optional)
        """
        output = self.docker.compose.logs(
            services=[service], no_log_prefix=True, follow=follow, stream=True
        )
        for source, line in output:
            line = line.decode()
            if source == "stdout":
                if "[==".lower() in line.lower():
                    print(line)
                else:
                    richprint.stdout.print(line)

    def frappe_logs_till_start(self, status_msg=None):
        """
        The function `frappe_logs_till_start` prints logs until a specific line is found and then stops.
        """
        status_text = "Creating Site"

        if status_msg:
            status_text = status_msg

        richprint.change_head(status_text)
        try:
            output = self.docker.compose.logs(
                services=["frappe"], no_log_prefix=True, follow=True, stream=True
            )

            if self.quiet:
                exit_code = richprint.live_lines(
                    output,
                    padding=(0, 0, 0, 2),
                    stop_string="INFO supervisord started with pid",
                )
            else:
                for source, line in output:
                    if not source == "exit_code":
                        line = line.decode()

                        if "Updating files:".lower() in line.lower():
                            continue
                        if "[==".lower() in line.lower():
                            print(line)
                            continue
                        richprint.stdout.print(line)
                        if "INFO supervisord started with pid".lower() in line.lower():
                            break
        except DockerException as e:
            richprint.warning(f"{status_text}: Failed")

    def stop(self) -> bool:
        """
        The `stop` function stops containers and prints the status of the operation using the `richprint`
        module.
        """
        status_text = "Stopping Containers"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.stop(timeout=10, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.exit(f"{status_text}: Failed")

        # stopping worker containers
        if self.workers.exists():
            self.workers.stop()

    def down(self, remove_ophans=True, volumes=True, timeout=5) -> bool:
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
                    timeout=timeout,
                    stream=self.quiet,
                )
                if self.quiet:
                    exit_code = richprint.live_lines(output, padding=(0, 0, 0, 2))
                richprint.print(f"{status_text}: Done")
            except DockerException as e:
                richprint.exit(f"{status_text}: Failed")

    def remove(self) -> bool:
        """
        The `remove` function removes containers and then recursively  site directories.
        """
        # TODO handle low leverl error like read only, write only etc
        if self.composefile.exists():
            status_text = "Removing Containers"
            richprint.change_head(status_text)
            try:
                output = self.docker.compose.down(
                    remove_orphans=True, volumes=True, timeout=2, stream=self.quiet
                )
                if self.quiet:
                    exit_code = richprint.live_lines(output, padding=(0, 0, 0, 2))
                richprint.print(f"Removing Containers: Done")
            except DockerException as e:
                richprint.exit(f"{status_text}: Failed")
        richprint.change_head(f"Removing Dirs")
        try:
            shutil.rmtree(self.path)
        except Exception as e:
            richprint.error(e)
            richprint.exit(f"Please remove {self.path} manually")
        richprint.change_head(f"Removing Dirs: Done")

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
        non_bash_supported = [
            "redis-cache",
            "redis-cache",
            "redis-socketio",
            "redis-queue",
        ]
        try:
            if not container in non_bash_supported:
                if container == "frappe":
                    shell_path = "/usr/bin/zsh"
                else:
                    shell_path = "/bin/bash"
                if user:
                    self.docker.compose.exec(container, user=user, command=shell_path)
                else:
                    self.docker.compose.exec(container, command=shell_path)
            else:
                if user:
                    self.docker.compose.exec(container, user=user, command="sh")
                else:
                    self.docker.compose.exec(container, command="sh")
        except DockerException as e:
            richprint.warning(f"Shell exited with error code: {e.return_code}")

    def get_site_installed_apps(self):
        """
        The function executes a command to list the installed apps for a specific site and prints the
        output.
        """
        command = f"/opt/.pyenv/shims/bench --site {self.name} list-apps"
        # command = f'which bench'
        output = self.docker.compose.exec(
            "frappe",
            user="frappe",
            workdir="/workspace/frappe-bench",
            command=command,
            stream=True,
        )
        for source, line in output:
            line = line.decode()
            pass

    def bench_dev_server_logs(self, follow=False):
        """
        This function is used to tail logs found at /workspace/logs/bench-start.log.
        :param follow: Bool detemines whether to follow the log file for changes
        """
        bench_start_log_path = (
            self.path / "workspace" / "frappe-bench" / "logs" / "web.dev.log"
        )

        if bench_start_log_path.exists() and bench_start_log_path.is_file():
            with open(bench_start_log_path, "r") as bench_start_log:
                bench_start_log_data = log_file(bench_start_log, follow=follow)
                for line in bench_start_log_data:
                    richprint.stdout.print(line)
        else:
            richprint.error(f"Log file not found: {bench_start_log_path}")

    def is_site_created(self, retry=60, interval=1) -> bool:
        import requests
        from time import sleep

        i = 0
        while i < retry:
            try:
                host_header = {"Host": f"{self.name}"}
                response = requests.get(url=f"http://127.0.0.1", headers=host_header)
                if response.status_code == 200:
                    return True
                else:
                    raise Exception("Site not working.")
            except Exception as e:
                sleep(interval)
                i += 1
                continue

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
            return {}

    def get_host_port_binds(self):
        try:
            output = self.docker.compose.ps(all=True, format="json", stream=True)
            status: list = []
            for source, line in output:
                if source == "stdout":
                    current_status = json.loads(line.decode())
                    if type(current_status) == dict:
                        status.append(current_status)
                    else:
                        status += current_status
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

    def is_service_running(self, service):
        running_status = self.get_services_running_status()
        if running_status[service] == "running":
            return True
        else:
            return False

    def sync_workers_compose(self):
        self.regenerate_supervisor_conf()
        are_workers_not_changed = self.workers.is_expected_worker_same_as_template()
        if are_workers_not_changed:
            richprint.print("Workers configuration remains unchanged.")
            return

        self.workers.generate_compose()
        self.workers.start()

    def regenerate_supervisor_conf(self):
        if self.name:
            richprint.change_head("Regenerating supervisor.conf.")
            backup = False
            backup_list = []

            # take backup
            if self.workers.supervisor_config_path.exists():
                shutil.copy(
                    self.workers.supervisor_config_path,
                    self.workers.supervisor_config_path.parent / "supervisor.conf.bak",
                )
                for file_path in self.workers.config_dir.iterdir():
                    file_path_abs = str(file_path.absolute())

                    if not file_path.is_file():
                        continue

                    if file_path_abs.endswith(".fm.supervisor.conf"):
                        from_path = file_path
                        to_path = file_path.parent / f"{file_path.name}.bak"
                        shutil.copy(from_path, to_path)
                        backup_list.append((from_path, to_path))

                backup = True

            # generate the supervisor.conf
            try:
                bench_setup_supervisor_command = "bench setup supervisor --skip-redis --skip-supervisord --yes --user frappe"

                output = self.docker.compose.exec(
                    service="frappe",
                    command=bench_setup_supervisor_command,
                    stream=True,
                    user="frappe",
                    workdir="/workspace/frappe-bench",
                )
                richprint.live_lines(output, padding=(0, 0, 0, 2))

                generate_split_config_command = (
                    "/scripts/divide-supervisor-conf.py config/supervisor.conf"
                )

                output = self.docker.compose.exec(
                    service="frappe",
                    command=generate_split_config_command,
                    stream=True,
                    user="frappe",
                    workdir="/workspace/frappe-bench",
                )

                richprint.live_lines(output, padding=(0, 0, 0, 2))

                return True
            except DockerException as e:
                richprint.error(f"Failure in generating, supervisor.conf file.{e}")

                if backup:
                    richprint.print("Rolling back to previous workers configuration.")
                    shutil.copy(
                        self.workers.supervisor_config_path.parent
                        / "supervisor.conf.bak",
                        self.workers.supervisor_config_path,
                    )

                    for from_path, to_path in backup_list:
                        shutil.copy(to_path, from_path)

                return False

    def get_bench_installed_apps_list(self):
        apps_json_file = (
            self.path / "workspace" / "frappe-bench" / "sites" / "apps.json"
        )

        apps_data: dict = {}

        if not apps_json_file.exists():
            return {}

        with open(apps_json_file, "r") as f:
            apps_data = json.load(f)

        return apps_data

    def get_site_db_info(self):
        db_info = {}

        site_config_file = (
            self.path
            / "workspace"
            / "frappe-bench"
            / "sites"
            / self.name
            / "site_config.json"
        )

        if site_config_file.exists():
            with open(site_config_file, "r") as f:
                site_config = json.load(f)
                db_info["name"] = site_config["db_name"]
                db_info["user"] = site_config["db_name"]
                db_info["password"] = site_config["db_password"]
        else:
            db_info["name"] = str(self.name).replace(".", "-")
            db_info["user"] = str(self.name).replace(".", "-")
            db_info["password"] = None

        return db_info

    def add_user(self, service, db_user, db_password, force=False, timeout=5):
        db_host = "127.0.0.1"

        site_db_info = self.get_site_db_info()
        site_db_name = site_db_info["name"]
        site_db_user = site_db_info["user"]
        site_db_pass = site_db_info["password"]

        remove_db_user = f"/usr/bin/mariadb -P3306 -h{db_host} -u{db_user} -p'{db_password}' -e 'DROP USER `{site_db_user}`@`%`;'"
        add_db_user = f"/usr/bin/mariadb -h{db_host} -P3306 -u{db_user} -p'{db_password}' -e 'CREATE USER `{site_db_user}`@`%` IDENTIFIED BY \"{site_db_pass}\";'"
        grant_user = f"/usr/bin/mariadb -h{db_host} -P3306 -u{db_user} -p'{db_password}' -e 'GRANT ALL PRIVILEGES ON `{site_db_name}`.* TO `{site_db_user}`@`%`;'"
        SHOW_db_user = f"/usr/bin/mariadb -P3306-h{db_host} -u{db_user} -p'{db_password}' -e 'SELECT User, Host FROM mysql.user;'"
        #
        import time

        check_connection_command = f"/usr/bin/mariadb -h{db_host} -u{db_user} -p'{db_password}' -e 'SHOW DATABASES;'"

        i = 0
        connected = False

        error = None
        while i < timeout:
            try:
                time.sleep(5)
                output = self.docker.compose.exec(
                    service,
                    command=check_connection_command,
                    stream=self.quiet,
                    stream_only_exit_code=True,
                )
                if output == 0:
                    connected = True
            except DockerException as e:
                error = e
                pass

            i += 1

        if not connected:
            raise SiteDatabaseAddUserException(
                self.name, f"Not able to start db: {error}"
            )

        removed = True
        try:
            output = self.docker.compose.exec(
                service,
                command=remove_db_user,
                stream=self.quiet,
                stream_only_exit_code=True,
            )
        except DockerException as e:
            removed = False
            if "error 1396" in str(e.stderr).lower():
                removed = True

        if removed:
            try:
                output = self.docker.compose.exec(
                    service,
                    command=add_db_user,
                    stream=self.quiet,
                    stream_only_exit_code=True,
                )
                output = self.docker.compose.exec(
                    service,
                    command=grant_user,
                    stream=self.quiet,
                    stream_only_exit_code=True,
                )
                richprint.print(f"Recreated user {site_db_user}")
            except DockerException as e:
                raise SiteDatabaseAddUserException(
                    self.name, f"Database user creation failed: {e}"
                )

    def remove_secrets(self):
        richprint.print(f"Removing Secrets", emoji_code=":construction:")
        richprint.change_head(f"Removing Secrets")

        running = False
        if self.running():
            running = True
            self.stop()

        self.composefile.remove_secrets_from_container("frappe")
        self.composefile.remove_root_secrets_compose()
        self.composefile.write_to_file()

        if running:
            self.start()
            self.frappe_logs_till_start(status_msg='Starting Site')

        richprint.print(f"Removing Secrets: Done")

    def remove_database_and_user(self):
        """
        This function is used to remove db and user of the site at self.name and path at self.path.
        """
        site_db_info = self.get_site_db_info()
        if "name" in site_db_info:
            db_name = site_db_info["name"]
            db_user = site_db_info["user"]
            self.services.remove_db_user(db_name)
            self.services.remove_db(db_user)
