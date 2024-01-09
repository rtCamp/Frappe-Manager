from copy import deepcopy
import importlib
import shutil
import re
import json
from typing import List, Type
from pathlib import Path

from frappe_manager.docker_wrapper import DockerClient, DockerException

from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.site_manager.workers_manager.SiteWorker import SiteWorkers
from frappe_manager.site_manager.utils import log_file, get_container_name_prefix
from frappe_manager.utils import host_run_cp


class Site:
    def __init__(self, path: Path, name: str, verbose: bool = False):
        self.path = path
        self.name = name
        self.quiet = not verbose
        self.init()

    def init(self):
        """
        The function checks if the Docker daemon is running and exits with an error message if it is not.
        """
        self.composefile = ComposeFile(self.path / "docker-compose.yml")
        self.docker = DockerClient(compose_file_path=self.composefile.compose_path)
        self.workers = SiteWorkers(self.path,self.name,self.quiet)

        if not self.docker.server_running():
            richprint.exit("Docker daemon not running. Please start docker service.")

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
                    if not 'ENVIRONMENT' in envs['frappe']:
                        envs['frappe']['ENVIRONMENT'] = 'dev'

                    envs['frappe']['CONTAINER_NAME_PREFIX'] = get_container_name_prefix(self.name)
                    envs['frappe']['MARIADB_ROOT_PASS'] = 'root'

                    envs['nginx']['VIRTUAL_HOST'] = self.name

                    import os
                    envs_user_info = {}
                    userid_groupid:dict = {"USERID": os.getuid(), "USERGROUP": os.getgid() }

                    env_user_info_container_list = ['frappe','schedule','socketio']

                    for env in env_user_info_container_list:
                        envs_user_info[env] = deepcopy(userid_groupid)

                    # overwrite user for each invocation
                    users = {"nginx":{
                                    "uid": os.getuid(),
                                    "gid": os.getgid()
                                    }
                            }

                    # load template
                    self.composefile.yml = self.composefile.load_template()

                    # set all the payload
                    self.composefile.set_all_envs(envs)
                    self.composefile.set_all_envs(envs_user_info)
                    self.composefile.set_all_labels(labels)
                    self.composefile.set_all_users(users)
                    # self.composefile.set_all_extrahosts(extrahosts)

                    self.create_compose_dirs()
                    self.composefile.set_network_alias("nginx", "site-network", [self.name])
                    self.composefile.set_container_names(get_container_name_prefix(self.name))
                    fm_version = importlib.metadata.version("frappe-manager")
                    self.composefile.set_version(fm_version)
                    self.composefile.set_top_networks_name("site-network",get_container_name_prefix(self.name))
                    self.composefile.write_to_file()
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
        fm_version = importlib.metadata.version("frappe-manager")
        self.composefile.set_version(fm_version)
        self.composefile.set_top_networks_name("site-network",get_container_name_prefix(self.name))
        self.composefile.write_to_file()

    def create_site_dir(self):
        # create site dir
        self.path.mkdir(parents=True, exist_ok=True)

    def create_compose_dirs(self) -> bool:
        """
        The function `create_dirs` creates two directories, `workspace` and `certs`, within a specified
        path.
        """
        richprint.change_head("Creating Compose directories")

        # create compose bind dirs -> workspace
        workspace_path = self.path / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        configs_path = self.path / 'configs'
        configs_path.mkdir(parents=True, exist_ok=True)

        # create nginx dirs
        nginx_dir = configs_path / "nginx"
        nginx_dir.mkdir(parents=True, exist_ok=True)

        nginx_poluate_dir = ['conf']
        nginx_image = self.composefile.yml['services']['nginx']['image']

        for directory in nginx_poluate_dir:
            new_dir = nginx_dir / directory
            if not new_dir.exists():
                new_dir_abs = str(new_dir.absolute())
                host_run_cp(nginx_image,source="/etc/nginx",destination=new_dir_abs,docker=self.docker)

        nginx_subdirs = ['logs','cache','run']
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
            richprint.exit(f"{status_text}: Failed",error_msg=e)

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

    def frappe_logs_till_start(self,status_msg = None):
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
                        if "[==".lower() in line.lower():
                            print(line)
                        else:
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
                richprint.print(f"Removing Containers: Done")
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
        bench_start_log_path = self.path / "workspace" / "frappe-bench" / 'logs' / "web.dev.log"

        if bench_start_log_path.exists() and bench_start_log_path.is_file():
            with open(bench_start_log_path, "r") as bench_start_log:
                bench_start_log_data = log_file(bench_start_log, follow=follow)
                for line in bench_start_log_data:
                    richprint.stdout.print(line)
        else:
            richprint.error(f"Log file not found: {bench_start_log_path}")

    def is_site_created(self, retry=30, interval=1) -> bool:
        import requests
        from time import sleep

        i = 0
        while i < retry:
            try:
                response = requests.get(f"http://{self.name}")
            except Exception:
                return False
            if response.status_code == 200:
                return True
            else:
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
            richprint.exit(f"{e.stdout}{e.stderr}")

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

    def is_service_running(self, service):
        running_status = self.get_services_running_status()
        if running_status[service] == "running":
            return True
        else:
            return False

    def sync_workers_compose(self):
        are_workers_not_changed = self.workers.is_expected_worker_same_as_template()
        if not are_workers_not_changed:
            self.workers.generate_compose()
            self.workers.start()
        else:
            richprint.print("Workers configuration remains unchanged.")


    def regenerate_supervisor_conf(self):
        richprint.change_head("Regenerating supervisor.conf.")
        backup = False
        # take backup
        if self.workers.supervisor_config_path.exists():
            shutil.copy(self.workers.supervisor_config_path, self.workers.supervisor_config_path.parent / "supervisor.conf.bak")
            for file_path in self.workers.config_dir.iterdir():
                file_path_abs = str(file_path.absolute())
                if file_path.is_file():
                    if '.workers.fm.supervisor.conf' in file_path_abs:
                        shutil.copy(file_path, file_path.parent / f"{file_path.name}.bak")
            backup = True

        # generate the supervisor.conf
        try:
            bench_setup_supervisor_command = 'bench setup supervisor --skip-redis --skip-supervisord --yes --user frappe'

            output = self.docker.compose.exec(
                service='frappe',
                command=bench_setup_supervisor_command,
                stream=True,
                user='frappe',
                workdir='/workspace/frappe-bench'
            )
            richprint.live_lines(output, padding=(0, 0, 0, 2))

            generate_split_config_command = '/scripts/divide-supervisor-conf.py config/supervisor.conf'

            output = self.docker.compose.exec(
            service='frappe',
            command=generate_split_config_command ,
            stream=True,
            user='frappe',
            workdir='/workspace/frappe-bench'
            )
            richprint.live_lines(output, padding=(0, 0, 0, 2))
            return True
        except DockerException as e:
            richprint.error("Failure in generating, supervisor.conf file.")
            if backup:
                richprint.print("Rolling back to previous workers configuration.")
                shutil.copy(self.workers.supervisor_config_path.parent / "supervisor.conf.bak", self.workers.supervisor_config_path)

                for file_path in self.workers.config_dir.iterdir():
                    file_path_abs = str(file_path.absolute())
                    if file_path.is_file():
                        if '.workers.fm.supervisor.conf.bak' in file_path_abs:
                            shutil.copy(file_path, file_path.parent / f"{file_path.name}".replace(".back",""))
            return False
