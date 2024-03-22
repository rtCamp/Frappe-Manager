import importlib
import shutil
import json
from pathlib import Path
from frappe_manager.docker_wrapper.DockerClient import DockerClient
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.site_manager.site_exceptions import (
    SiteDatabaseAddUserException,
    SiteException,
)
from frappe_manager.site_manager.workers_manager.SiteWorker import SiteWorkers
from frappe_manager.utils.helpers import log_file, get_container_name_prefix
from frappe_manager.utils.docker import host_run_cp
from frappe_manager.utils.site import is_fqdn


class Site:
    def __init__(self, path: Path, name: str, verbose: bool = False, services=None) -> None:
        self.path = path
        self.name = name
        self.quiet = not verbose
        self.services = services
        self.init()

    def init(self):
        self.composefile = ComposeFile(self.path / "docker-compose.yml")

        self.docker = DockerClient(compose_file_path=self.composefile.compose_path)
        self.workers = SiteWorkers(self.path, self.name, self.quiet)

        if self.workers.exists():
            if not self.workers.running():
                if self.running():
                    self.workers.start()

    def exists(self):
        return self.path.exists()

    def validate_sitename(self) -> bool:
        sitename = self.name
        match = is_fqdn(sitename)

        if not match:
            richprint.error(f"The {sitename} must follow Fully Qualified Domain Name (FQDN) format.", exception=SiteException(self, f"Valid FQDN site name not provided."))

        return True

    def get_frappe_container_hex(self) -> None | str:
        """
        Returns the hexadecimal representation of the frappe container name.

        Returns:
            str: The hexadecimal representation of the frappe container name.
        """
        container_name = self.composefile.get_container_names()
        return container_name["frappe"].encode().hex()

    def common_site_config_set(self, config: dict):
        """
        Sets the values in the common_site_config.json file.

        Args:
            config (dict): A dictionary containing the key-value pairs to be set in the common_site_config.json file.

        Returns:
            bool: True if the values are successfully set, False otherwise.
        """
        common_site_config_path = self.path / "workspace/frappe-bench/sites/common_site_config.json"

        if not common_site_config_path.exists():
            return False

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
            return False

    def generate_compose(self, inputs: dict) -> None:
        """
        Generates the compose file for the site based on the given inputs.

        Args:
            inputs (dict): A dictionary containing the inputs for generating the compose file.

        Returns:
            None
        """
        if "environment" in inputs.keys():
            environments: dict = inputs["environment"]
            self.composefile.set_all_envs(environments)

        if "labels" in inputs.keys():
            labels: dict = inputs["labels"]
            self.composefile.set_all_labels(labels)

        if "user" in inputs.keys():
            user: dict = inputs["user"]
            for container_name in user.keys():
                uid = user[container_name]["uid"]
                gid = user[container_name]["gid"]
                self.composefile.set_user(container_name, uid, gid)

        self.composefile.set_network_alias("nginx", "site-network", [self.name])
        self.composefile.set_container_names(get_container_name_prefix(self.name))
        self.composefile.set_secret_file_path(
            "db_root_password",
            self.services.composefile.get_secret_file_path("db_root_password"),
        )

        fm_version = importlib.metadata.version("frappe-manager")

        self.composefile.set_version(fm_version)
        self.composefile.set_top_networks_name("site-network", get_container_name_prefix(self.name))
        self.composefile.write_to_file()

    def create_site_dir(self):
        self.path.mkdir(parents=True, exist_ok=True)

    def sync_site_common_site_config(self):
        """
        Syncs the common site configuration with the global database information and container prefix.

        This function sets the common site configuration data including the socketio port, database host and port,
        and the Redis cache, queue, and socketio URLs.
        """
        global_db_info = self.services.get_database_info()
        container_prefix = get_container_name_prefix(self.name)

        # set common site config
        common_site_config_data = {
            "socketio_port": "80",
            "db_host": global_db_info["host"],
            "db_port": global_db_info["port"],
            "redis_cache": f"redis://{container_prefix}-redis-cache:6379",
            "redis_queue": f"redis://{container_prefix}-redis-queue:6379",
            "redis_socketio": f"redis://{container_prefix}-redis-cache:6379",
        }
        self.common_site_config_set(common_site_config_data)

    def create_compose_dirs(self) -> bool:
        """
        Creates the necessary directories for the Compose setup.

        Returns:
            bool: True if the directories are created successfully, False otherwise.
        """
        richprint.change_head("Creating Compose directories")

        frappe_image = self.composefile.yml["services"]["frappe"]["image"]

        workspace_path = self.path / "workspace"
        workspace_path_abs = str(workspace_path.absolute())

        host_run_cp(
            frappe_image,
            source="/workspace",
            destination=workspace_path_abs,
            docker=self.docker,
        )

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

        return True

    def start(self, force: bool = False) -> bool:
        """
        Starts the Docker containers for the site.

        Returns:
            bool: True if the containers were started successfully, False otherwise.
        """
        status_text = "Starting Docker Containers"
        richprint.change_head(status_text)

        try:
            output = self.docker.compose.up(detach=True, pull="never", force_recreate=force, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.error(f"{status_text}: Failed", exception=e)

        # start workers if exists
        if self.workers.exists():
            self.workers.start(force=force)

        return True

    def pull(self):
        """
        Pull docker images.
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
            raise e

    def logs(self, service: str, follow: bool = False):
        """
        Retrieve and print the logs for a specific service.

        Args:
            service (str): The name of the service.
            follow (bool, optional): Whether to continuously follow the logs. Defaults to False.
        """
        output = self.docker.compose.logs(services=[service], no_log_prefix=True, follow=follow, stream=True)
        for source, line in output:
            line = line.decode()
            if source == "stdout":
                if "[==".lower() in line.lower():
                    print(line)
                else:
                    richprint.stdout.print(line)

    def frappe_logs_till_start(self, status_msg=None):
        """
        Retrieves and prints the logs of the 'frappe' service until site supervisor starts.

        Args:
            status_msg (str, optional): Custom status message to display. Defaults to None.
        """
        status_text = "Creating Site"

        if status_msg:
            status_text = status_msg

        richprint.change_head(status_text)
        try:
            output = self.docker.compose.logs(
                services=["frappe"],
                no_log_prefix=True,
                follow=True,
                stream=True,
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
        Stop the site by stopping the containers.

        Returns:
            bool: True if the site is successfully stopped, False otherwise.
        """
        status_text = "Stopping Containers"
        richprint.change_head(status_text)
        try:
            output = self.docker.compose.stop(timeout=10, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print(f"{status_text}: Done")
        except DockerException as e:
            richprint.error(f"{status_text}: Failed", exception=e)

        # stopping worker containers
        if self.workers.exists():
            self.workers.stop()

    def down(self, remove_ophans=True, volumes=True, timeout=5) -> bool:
        """
        Stops and removes the containers for the site.

        Args:
            remove_ophans (bool, optional): Whether to remove orphan containers. Defaults to True.
            volumes (bool, optional): Whether to remove volumes. Defaults to True.
            timeout (int, optional): Timeout in seconds for stopping the containers. Defaults to 5.

        Returns:
            bool: True if the containers were successfully stopped and removed, False otherwise.
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
                richprint.error(f"{status_text}: Failed", exception=e)

    def remove(self) -> bool:
        """
        Removes the site by stopping and removing the containers associated with it,
        and deleting the site directory.

        Returns:
            bool: True if the site is successfully removed, False otherwise.
        """
        # TODO handle low level errors like read only, write only, etc.
        if self.composefile.exists():
            status_text = "Removing Containers"
            richprint.change_head(status_text)
            try:
                output = self.docker.compose.down(
                    remove_orphans=True,
                    volumes=True,
                    timeout=2,
                    stream=self.quiet,
                )
                if self.quiet:
                    exit_code = richprint.live_lines(output, padding=(0, 0, 0, 2))
                richprint.print(f"Removing Containers: Done")
            except DockerException as e:
                richprint.error(f"{status_text}: Failed", exception=e)

        richprint.change_head(f"Removing Dirs")

        try:
            shutil.rmtree(self.path)
        except PermissionError as e:
            images = self.composefile.get_all_images()
            if "frappe" in images:
                try:
                    frappe_image = images["frappe"]
                    frappe_image = f"{frappe_image['name']}:{frappe_image['tag']}"
                    output = self.docker.run(
                        image=frappe_image,
                        entrypoint="/bin/sh",
                        command="-c 'chown -R frappe:frappe .'",
                        volume=f"{self.path}/workspace:/workspace",
                        stream=True,
                        stream_only_exit_code=True,
                    )

                    shutil.rmtree(self.path)

                except Exception:
                    richprint.error(e)
                    richprint.exit(f"Please remove {self.path} manually")

        richprint.change_head(f"Removing Dirs: Done")

    def shell(self, container: str, user: str | None = None):
        """
        Open a shell inside a Docker container.

        Args:
            container (str): The name of the Docker container.
            user (str, optional): The user to run the shell as. Defaults to None.
        """
        richprint.stop()

        non_bash_supported = ["redis-cache", "redis-socketio", "redis-queue"]

        shell_path = "/bin/bash" if container not in non_bash_supported else "sh"

        exec_args = {"service": container, "command": shell_path}

        if container == "frappe":
            exec_args["command"] = "/usr/bin/zsh"
            exec_args["workdir"] = "/workspace/frappe-bench"

        if user:
            exec_args["user"] = user

        try:
            self.docker.compose.exec(**exec_args)
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
        Display the logs of the development server in the Frappe Bench.

        Args:
            follow (bool, optional): Whether to continuously follow the logs. Defaults to False.
        """
        bench_start_log_path = self.path / "workspace" / "frappe-bench" / "logs" / "web.dev.log"

        if bench_start_log_path.exists() and bench_start_log_path.is_file():
            with open(bench_start_log_path, "r") as bench_start_log:
                bench_start_log_data = log_file(bench_start_log, follow=follow)
                for line in bench_start_log_data:
                    print(line)
        else:
            richprint.error(f"Log file not found: {bench_start_log_path}")

    def is_site_created(self, retry=60, interval=1) -> bool:
        """
        Checks if the site is created and accessible.

        Args:
            retry (int): Number of times to retry the check.
            interval (int): Interval between each retry in seconds.

        Returns:
            bool: True if the site is created and accessible, False otherwise.
        """
        from time import sleep

        for _ in range(retry):
            try:
                # Execute curl command on frappe service
                result = self.docker.compose.exec(
                    service="frappe",
                    command=f"curl -I --max-time {retry} --connect-timeout {retry} http://localhost",
                    stream=True,
                )

                # Check if the site is working
                for source, line in result:
                    if "HTTP/1.1 200 OK" in line.decode():
                        return True
            except Exception as e:
                sleep(interval)

        return False

    def running(self) -> bool:
        """
        Check if all services specified in the compose file are running.

        Returns:
            bool: True if all services are running, False otherwise.
        """
        services = self.composefile.get_services_list()
        running_status = self.get_services_running_status()

        if not running_status:
            return False

        for service in services:
            try:
                if not running_status[service] == "running":
                    return False
            except KeyError:
                return False

        return True

    def get_services_running_status(self) -> dict:
        """
        Get the running status of services in the Docker Compose file.

        Returns:
            A dictionary containing the running status of services.
            The keys are the service names, and the values are the container states.
        """
        services = self.composefile.get_services_list()
        containers = self.composefile.get_container_names().values()
        services_status = {}
        try:
            output = self.docker.compose.ps(service=services, format="json", all=True, stream=True)
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
        """
        Get the list of published ports on the host for all containers.

        Returns:
            list: A list of published ports on the host.
        """
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

                generate_split_config_command = "/scripts/divide-supervisor-conf.py config/supervisor.conf"

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
                        self.workers.supervisor_config_path.parent / "supervisor.conf.bak",
                        self.workers.supervisor_config_path,
                    )

                    for from_path, to_path in backup_list:
                        shutil.copy(to_path, from_path)

                return False

    def get_bench_installed_apps_list(self):
        apps_json_file = self.path / "workspace" / "frappe-bench" / "sites" / "apps.json"
        apps_data: dict = {}
        if not apps_json_file.exists():
            return {}
        with open(apps_json_file, "r") as f:
            apps_data = json.load(f)
        return apps_data

    def get_site_db_info(self):
        db_info = {}

        site_config_file = self.path / "workspace" / "frappe-bench" / "sites" / self.name / "site_config.json"

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
            raise SiteDatabaseAddUserException(self.name, f"Not able to start db: {error}")

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
                raise SiteDatabaseAddUserException(self.name, f"Database user creation failed: {e}")

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
            self.frappe_logs_till_start(status_msg="Starting Site")

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
