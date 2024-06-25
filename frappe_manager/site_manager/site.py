import copy
import time
import itertools
from datetime import datetime
import shlex
import shutil
import json
import subprocess
from typing import Any, Dict, List, Optional
from pathlib import Path
from frappe_manager.site_manager.bench_operations import BenchOperations
from rich.table import Table
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.logger import log
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager import VSCODE_LAUNCH_JSON, VSCODE_SETTINGS_JSON, VSCODE_TASKS_JSON
from frappe_manager.site_manager.admin_tools import AdminTools
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.site_manager.site_exceptions import (
    BenchAttachTocontainerFailed,
    BenchException,
    BenchFailedToRemoveDevPackages,
    BenchFrappeServiceSupervisorNotRunning,
    BenchNotRunning,
    BenchOperationException,
    BenchRemoveDirectoryError,
    BenchSSLCertificateAlreadyIssued,
    BenchSSLCertificateNotIssued,
    BenchServiceNotRunning,
)
from frappe_manager.site_manager.workers_manager.SiteWorker import BenchWorkers
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.nginxproxymanager import NginxProxyManager
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.utils.helpers import (
    capture_and_format_exception,
    format_ssl_certificate_time_remaining,
    get_current_fm_version,
    log_file,
    get_container_name_prefix,
)
from frappe_manager.utils.docker import host_run_cp
from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    CLI_DIR,
    SiteServicesEnum,
)
from frappe_manager.utils.site import domain_level, generate_services_table, get_bench_db_connection_info


class Bench:
    def __init__(
        self,
        path: Path,
        name: str,
        bench_config: BenchConfig,
        compose_project: ComposeProject,
        services: ServicesManager,
        workers_check: bool = True,
        admin_tools_check: bool = True,
        verbose: bool = False,
    ) -> None:
        self.path = path
        self.name = name
        self.quiet = not verbose
        self.services = services
        self.backup_path = self.path / 'backups'
        self.bench_config: BenchConfig = bench_config
        self.compose_project: ComposeProject = compose_project
        self.logger = log.get_logger()
        self.proxy_manager: NginxProxyManager = NginxProxyManager('nginx', self.compose_project)
        self.admin_tools: AdminTools = AdminTools(self.name, self.path, self.proxy_manager)

        self.certificate_manager = SSLCertificateManager(
            certificate=self.bench_config.ssl,
            webroot_dir=self.proxy_manager.dirs.html.host,
            proxy_manager=services.proxy_manager,
        )
        self.benchops = BenchOperations(self)
        self.workers = BenchWorkers(self, not verbose)

        if workers_check:
            self.ensure_workers_running_if_available()

        if admin_tools_check:
            self.ensure_admin_tools_running_if_available()

    @classmethod
    def get_object(
        cls,
        bench_name: str,
        services: ServicesManager,
        benches_path: Path = CLI_BENCHES_DIRECTORY,
        bench_config_file_name: str = CLI_BENCH_CONFIG_FILE_NAME,
        workers_check: bool = False,
        admin_tools_check: bool = False,
        verbose: bool = False,
    ) -> 'Bench':
        if domain_level(bench_name) == 0:
            bench_name = bench_name + ".localhost"

        bench_path = benches_path / bench_name
        bench_config_path: Path = bench_path / bench_config_file_name

        compose_file_manager = ComposeFile(bench_path / "docker-compose.yml")
        compose_project: ComposeProject = ComposeProject(compose_file_manager, verbose=verbose)

        bench_config: BenchConfig = BenchConfig.import_from_toml(bench_config_path)

        parms: Dict[str, Any] = {
            'name': bench_name,
            'path': bench_path,
            'bench_config': bench_config,
            'compose_project': compose_project,
            'services': services,
            'workers_check': workers_check,
            'admin_tools_check': admin_tools_check,
        }
        return cls(**parms)

    def sync_bench_config_configuration(self):
        # set developer_mode based on config
        self.common_bench_config_set({'developer_mode': self.bench_config.developer_mode})

        # dev or prod
        self.switch_bench_env()

        # ssl
        certificate_updated = self.update_certificate(self.bench_config.ssl, raise_error=False)
        if certificate_updated:
            richprint.print("Certificate Updated.")

        # admin tools
        if self.bench_config.admin_tools:
            if not self.admin_tools.compose_project.compose_file_manager.compose_path.exists():
                self.sync_admin_tools_compose()
            else:
                self.admin_tools.enable(force_configure=True)
            richprint.print("Enabled Admin-tools.")

        else:
            if not self.admin_tools.compose_project.compose_file_manager.compose_path.exists():
                richprint.print("Admin tools is already disabled.")
            else:
                self.admin_tools.disable()
                richprint.print("Disabled Admin-tools.")

        self.restart_frappe_server()

    def save_bench_config(self):
        richprint.change_head("Saving bench config changes")
        self.bench_config.export_to_toml(self.bench_config.root_path)
        richprint.print("Saved bench config.")

    @property
    def exists(self):
        return self.path.exists()

    @property
    def frappe_container_name_as_hex(self) -> str:
        """
        Returns the hexadecimal representation of the frappe container name.
        """
        container_name = self.compose_project.compose_file_manager.get_container_names()
        return container_name["frappe"].encode().hex()

    def create(self, is_template_bench: bool = False):
        """
        Creates a new bench using the provided template inputs.

        Args:
            template_inputs (dict): A dictionary containing the template inputs.

        Returns:
            None
        """
        self.benchops.check_required_docker_images_available()

        try:
            richprint.change_head("Creating Bench Directory")
            self.path.mkdir(parents=True, exist_ok=True)

            richprint.change_head("Generating bench compose")
            self.generate_compose(self.bench_config.export_to_compose_inputs())
            self.create_compose_dirs()

            if is_template_bench:
                global_db_info = self.services.database_manager.database_server_info
                self.sync_bench_common_site_config(global_db_info.host, global_db_info.port)
                self.save_bench_config()
                richprint.print(f"Created template bench: {self.name}", emoji_code=":white_check_mark:")
                return

            richprint.change_head("Starting bench services")
            self.compose_project.start_service(force_recreate=True)
            richprint.print("Started bench services.")

            richprint.change_head("Creating bench and bench site.")
            self.benchops.create_fm_bench()
            self.sync_bench_config_configuration()

            richprint.change_head("Configuring bench workers.")
            self.sync_workers_compose(force_recreate=True, setup_supervisor=False)
            richprint.change_head("Configuring bench workers.")
            richprint.update_live()

            self.save_bench_config()

            richprint.change_head("Commencing site status check")

            # check if bench is created
            if not self.is_bench_created():
                raise Exception("Bench site is inactive or unresponsive.")

            richprint.print("Bench site is active and responding.")

            self.logger.info(f"{self.name}: Bench site is active and responding.")

            self.info()

            if ".localhost" not in self.name:
                richprint.print(
                    "Please note that You will have to add a host entry to your system's hosts file to access the bench locally."
                )

        except Exception as e:
            richprint.stop()

            richprint.error(f"[red][bold]Error Occured: [/bold][/red]{e}")

            exception_traceback_str = capture_and_format_exception()

            logger = log.get_logger()

            logger.error(f"{self.name}: NOT WORKING\n Exception: {exception_traceback_str}")

            log_path = CLI_DIR / "logs" / "fm.log"

            error_message = [
                "There has been some error creating/starting the bench.",
                f":mag: Please check the logs at {log_path}",
            ]

            richprint.error("\n".join(error_message))

            if self.exists:
                remove_status = self.remove_bench(default_choice=False)

                if not remove_status:
                    self.info()

    def common_bench_config_set(self, config: dict):
        """
        Sets the values in the common_site_config.json file.

        Args:
            config (dict): A dictionary containing the key-value pairs to be set in the common_site_config.json file.
        """
        common_bench_config_path = self.path / "workspace/frappe-bench/sites/common_site_config.json"

        if not common_bench_config_path.exists():
            raise BenchException(self.name, message='common_site_config.json not found.')

        common_site_config = {}

        with open(common_bench_config_path, "r") as f:
            common_site_config = json.load(f)

        for key, value in config.items():
            common_site_config[key] = value

        with open(common_bench_config_path, "w") as f:
            json.dump(common_site_config, f)

    def get_common_bench_config(self):
        common_bench_config_path = self.path / "workspace/frappe-bench/sites/common_site_config.json"

        if not common_bench_config_path.exists():
            raise BenchException(self.name, message='common_site_config.json not found.')

        return json.loads(common_bench_config_path.read_text())

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
            self.compose_project.compose_file_manager.set_all_envs(environments)

        if "labels" in inputs.keys():
            labels: dict = inputs["labels"]
            self.compose_project.compose_file_manager.set_all_labels(labels)

        if "user" in inputs.keys():
            user: dict = inputs["user"]
            for container_name in user.keys():
                uid = user[container_name]["uid"]
                gid = user[container_name]["gid"]
                self.compose_project.compose_file_manager.set_user(container_name, uid, gid)

        self.compose_project.compose_file_manager.set_network_alias("nginx", "site-network", [self.name])
        self.compose_project.compose_file_manager.set_container_names(get_container_name_prefix(self.name))
        self.compose_project.compose_file_manager.set_version(get_current_fm_version())
        self.compose_project.compose_file_manager.set_top_networks_name(
            "site-network", get_container_name_prefix(self.name)
        )
        self.compose_project.compose_file_manager.write_to_file()

    def sync_bench_common_site_config(self, services_db_host: str, services_db_port: int):
        """
        Syncs the common site configuration with the global database information and container prefix.

        This function sets the common site configuration data including the socketio port, database host and port,
        and the Redis cache, queue, and socketio URLs.
        """
        container_prefix = get_container_name_prefix(self.name)

        # set common site config
        common_site_config_data = {
            "socketio_port": "80",
            "db_host": services_db_host,
            "db_port": services_db_port,
            "redis_cache": f"redis://{container_prefix}-redis-cache:6379",
            "redis_queue": f"redis://{container_prefix}-redis-queue:6379",
            "redis_socketio": f"redis://{container_prefix}-redis-cache:6379",
        }
        self.common_bench_config_set(common_site_config_data)

    def create_compose_dirs(self) -> bool:
        """
        Creates the necessary directories for the Compose setup.

        Returns:
            bool: True if the directories are created successfully, False otherwise.
        """
        richprint.change_head("Creating required directories")

        frappe_image = self.compose_project.compose_file_manager.yml["services"]["frappe"]["image"]

        workspace_path = self.path / "workspace"
        workspace_path_abs = str(workspace_path.absolute())

        host_run_cp(
            frappe_image,
            source="/workspace",
            destination=workspace_path_abs,
            docker=self.compose_project.docker,
        )

        configs_path = self.path / "configs"
        configs_path.mkdir(parents=True, exist_ok=True)

        # create nginx dirs
        nginx_dir = configs_path / "nginx"
        nginx_dir.mkdir(parents=True, exist_ok=True)

        nginx_poluate_dir = ["conf"]
        nginx_image = self.compose_project.compose_file_manager.yml["services"]["nginx"]["image"]

        for directory in nginx_poluate_dir:
            new_dir = nginx_dir / directory
            if not new_dir.exists():
                new_dir_abs = str(new_dir.absolute())
                host_run_cp(
                    nginx_image,
                    source="/etc/nginx",
                    destination=new_dir_abs,
                    docker=self.compose_project.docker,
                )

        nginx_subdirs = ["logs", "cache", "run", "html"]

        for directory in nginx_subdirs:
            new_dir = nginx_dir / directory
            new_dir.mkdir(parents=True, exist_ok=True)

        richprint.print("Created all required directories.")

        return True

    def start(self, force: bool = False):
        """
        Starts the bench.
        """

        self.benchops.check_required_docker_images_available()

        # Should be done in site manager ?
        global_db_info = self.services.database_manager.database_server_info
        self.sync_bench_common_site_config(global_db_info.host, global_db_info.port)

        richprint.change_head("Starting bench services")
        self.admin_tools.remove_nginx_location_config()
        self.compose_project.start_service(force_recreate=force)
        self.benchops.is_required_services_available()
        self.sync_workers_compose()
        self.sync_bench_config_configuration()
        self.save_bench_config()
        richprint.print("Started bench services.")

        # start workers if exists
        if self.workers.compose_project.compose_file_manager.exists():
            richprint.change_head("Starting bench workers services")
            self.workers.compose_project.start_service(force_recreate=force)
            richprint.print("Started bench workers services.")

    def frappe_logs_till_start(self):
        """
        Retrieves and prints the logs of the 'frappe' service until site supervisor starts.

        Args:
            status_msg (str, optional): Custom status message to display. Defaults to None.
        """
        output = self.compose_project.docker.compose.logs(
            services=["frappe"],
            no_log_prefix=True,
            no_color=True,
            follow=True,
            stream=True,
        )

        if self.quiet:
            richprint.live_lines(
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

    def stop(self) -> bool:
        """
        Stop the site by stopping the containers.

        Returns:
            bool: True if the site is successfully stopped, False otherwise.
        """
        richprint.change_head("Stopping bench services")
        self.compose_project.stop_service()
        richprint.print("Stopped bench services.")

        if self.workers.compose_project.compose_file_manager.exists():
            richprint.change_head("Starting bench workers services")
            self.workers.compose_project.stop_service()
            richprint.print("Started bench workers services")

        # stop admin_tools if exists
        if self.admin_tools.compose_project.compose_file_manager.exists():
            richprint.change_head("Stopped bench admin tools services")
            self.admin_tools.compose_project.stop_service()
            richprint.print("Stopped bench admin tools services.")

    def remove_containers_and_dirs(self):
        """
        Removes the site by stopping and removing the containers associated with it,
        and deleting the site directory.

        Returns:
            bool: True if the site is successfully removed, False otherwise.
        """
        # TODO handle low level errors like read only, write only, etc.
        if self.compose_project.compose_file_manager.exists():
            richprint.change_head("Removing bench containers.")
            self.compose_project.down_service(remove_ophans=True, volumes=True)
            richprint.print("Removed bench containers.")
        else:
            richprint.warning('Bench compose file not found. Skipping containers removal.')

        if self.workers.compose_project.compose_file_manager.exists():
            richprint.change_head("Removing bench workers containers.")
            self.workers.compose_project.down_service(remove_ophans=True, volumes=True)
            richprint.print("Removed bench workers containers.")
        else:
            richprint.warning('Bench workers compose file not found. Skipping containers removal.')

        if self.admin_tools.compose_project.compose_file_manager.exists():
            richprint.change_head("Removing bench admin tools containers.")
            self.admin_tools.compose_project.down_service(remove_ophans=True, volumes=True)
            richprint.print("Removed bench admin tools containers.")
        else:
            richprint.warning('Bench admin tools compose file not found. Skipping containers removal.')

        richprint.change_head("Removing all bench files and directories.")
        try:
            shutil.rmtree(self.path)
        except PermissionError:
            try:
                images = self.compose_project.compose_file_manager.get_all_images()
                if "frappe" in images:
                    frappe_image = images["frappe"]
                    frappe_image = f"{frappe_image['name']}:{frappe_image['tag']}"
                    self.compose_project.docker.run(
                        image=frappe_image,
                        entrypoint="/bin/sh",
                        command="-c 'chown -R frappe:frappe .'",
                        volume=f"{self.path}/workspace:/workspace",
                        stream=False,
                    )
                    shutil.rmtree(self.path)
            except Exception:
                raise BenchRemoveDirectoryError(self.name, self.path)

        richprint.print("Removed all bench files and directories.")

    def is_bench_created(self, retry=60, interval=1) -> bool:
        curl_command = 'curl -I --max-time {retry} --connect-timeout {retry} {headers} {url}'
        url = 'http://localhost'
        headers = ''
        if self.bench_config.environment_type == FMBenchEnvType.prod:
            headers = f"-H 'Host: {self.name}'"

        check_command = curl_command.format(retry=retry, headers=headers, url=url)

        for _ in range(retry):
            try:
                # Execute curl command on frappe service
                result = self.compose_project.docker.compose.exec(
                    service="frappe",
                    command=check_command,
                    stream=False,
                )
                for line in result.stdout:
                    if 'HTTP/1.1 200 OK' in line:
                        return True
            except Exception:
                time.sleep(interval)
        return False

    def sync_workers_compose(self, force_recreate: bool = False, setup_supervisor: bool = True):
        if setup_supervisor:
            workers_backup_manager = self.backup_workers_supervisor_conf()
            try:
                self.benchops.setup_supervisor(force=True)
            except BenchOperationException as e:
                self.backup_restore_workers_supervisor(workers_backup_manager)

        are_workers_not_changed = self.workers.is_new_workers_added()

        if are_workers_not_changed:
            richprint.print("Workers configuration remains unchanged.")
            return

        self.workers.generate_compose()
        self.workers.compose_project.start_service(force_recreate=force_recreate)

    def backup_restore_workers_supervisor(self, backup_manager: BackupManager):
        richprint.print("Rolling back to previous workers configuration.")
        for backup in backup_manager.backups:
            backup_manager.restore(backup, force=True)

    def backup_workers_supervisor_conf(self):
        backup_workers_manager = BackupManager(name='workers', backup_group_name='workers')
        backup_workers_manager.backup(self.workers.supervisor_config_path, bench_name=self.name)

        if self.workers.supervisor_config_path.exists():
            for file_path in self.workers.config_dir.iterdir():
                file_path_abs = str(file_path.absolute())
                if not file_path.is_file():
                    continue
                if file_path_abs.endswith(".fm.supervisor.conf"):
                    from_path = file_path
                    backup_workers_manager.backup(from_path, bench_name=self.name)
                    file_path.unlink()
        return backup_workers_manager

    def regenerate_workers_supervisor_conf(self):
        self.backup_workers_supervisor_conf()

    def get_bench_installed_apps_list(self):
        apps_json_file = self.path / "workspace" / "frappe-bench" / "sites" / "apps.json"
        apps_data: dict = {}
        if not apps_json_file.exists():
            return {}
        with open(apps_json_file, "r") as f:
            apps_data = json.load(f)
        return apps_data

    # this can be plugable
    def get_db_connection_info(self):
        return get_bench_db_connection_info(self.name, self.path)

    def create_certificate(self):
        self.certificate_manager.generate_certificate()
        self.save_bench_config()

    def has_certificate(self):
        return self.certificate_manager.has_certificate()

    def remove_certificate(self):
        self.certificate_manager.remove_certificate()
        self.bench_config.ssl = SSLCertificate(domain=self.name, ssl_type=SUPPORTED_SSL_TYPES.none)
        self.save_bench_config()

    def update_certificate(self, certificate: SSLCertificate, raise_error: bool = True):
        if certificate.ssl_type == SUPPORTED_SSL_TYPES.le:
            if self.has_certificate():
                if raise_error:
                    raise BenchSSLCertificateAlreadyIssued(self.name)
            else:
                self.certificate_manager.set_certificate(certificate)
                self.bench_config.ssl = certificate
                self.create_certificate()

        elif certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
            if self.has_certificate():
                self.remove_certificate()
            else:
                if not raise_error:
                    return
                raise BenchSSLCertificateNotIssued(self.name)

        return True

    def renew_certificate(self):
        if not self.has_certificate():
            raise BenchSSLCertificateNotIssued(self.name)

        if not self.compose_project.is_service_running('nginx'):
            raise BenchServiceNotRunning(self.name, 'nginx')

        self.certificate_manager.renew_certificate()

    def info(self):
        """
        Retrieves and displays information about the bench.

        This method retrieves various information about the site, such as site URL, site root, database details,
        Frappe username and password, root database user and password, and more. It then formats and displays
        this information using the richprint library.
        """

        richprint.change_head("Getting bench info")
        bench_db_info = self.get_db_connection_info()

        db_user = bench_db_info["name"]
        db_pass = bench_db_info["password"]

        services_db_info = self.services.database_manager.database_server_info
        bench_info_table = Table(show_lines=True, show_header=False, highlight=True)

        protocol = 'https' if self.has_certificate() else 'http'

        ssl_service_type = f'{self.bench_config.ssl.ssl_type.value}'

        if self.bench_config.ssl.ssl_type == SUPPORTED_SSL_TYPES.le:
            ssl_service_type = (
                f'[{self.bench_config.ssl.preferred_challenge.value}] {self.bench_config.ssl.ssl_type.value}'
            )

        data = {
            "Bench Url": f"{protocol}://{self.name}",
            "Bench Root": f"[link=file://{self.path.absolute()}]{self.path.absolute()}[/link]",
            "Frappe Username": "administrator",
            "Frappe Password": self.bench_config.admin_pass,
            "Root DB User": services_db_info.user,
            "Root DB Password": services_db_info.password,
            "Root DB Host": services_db_info.host,
            "DB Name": db_user,
            "DB User": db_user,
            "DB Password": db_pass,
            "Environment": self.bench_config.environment_type.value,
            "HTTPS": (
                f'{ssl_service_type.upper()} ({format_ssl_certificate_time_remaining(self.certificate_manager.get_certficate_expiry())})'
                if self.has_certificate()
                else 'Not Enabled'
            ),
        }

        if not self.bench_config.admin_tools:
            data['Admin Tools'] = 'Not Enabled'
        else:
            admin_tools_Table = Table(show_lines=True, show_edge=False, pad_edge=False, expand=True)
            admin_tools_Table.add_column("Tool")
            admin_tools_Table.add_column("URL")
            admin_tools_Table.add_row("Mailhog", f"{protocol}://{self.name}/mailhog")
            admin_tools_Table.add_row("Adminer", f"{protocol}://{self.name}/adminer")
            data['Admin Tools'] = admin_tools_Table

        bench_info_table.add_column(no_wrap=True)
        bench_info_table.add_column(no_wrap=True)

        for key in data.keys():
            bench_info_table.add_row(key, data[key])

        # get bench apps data
        apps_json = self.get_bench_installed_apps_list()

        if apps_json:
            bench_apps_list_table = Table(show_lines=True, show_edge=False, pad_edge=False, expand=True)

            bench_apps_list_table.add_column("App")
            bench_apps_list_table.add_column("Version")

            for app in apps_json.keys():
                bench_apps_list_table.add_row(app, apps_json[app]["version"])

            bench_info_table.add_row("Bench Apps", bench_apps_list_table)

        running_bench_services = self.compose_project.get_services_running_status()
        running_bench_workers = self.workers.compose_project.get_services_running_status()
        running_bench_admin_tools = self.admin_tools.compose_project.get_services_running_status()

        if running_bench_services:
            bench_services_table = generate_services_table(running_bench_services)
            bench_info_table.add_row("Bench Services", bench_services_table)

        if running_bench_workers:
            bench_workers_table = generate_services_table(running_bench_workers)
            bench_info_table.add_row("Bench Workers", bench_workers_table)

        if running_bench_admin_tools:
            bench_admin_table = generate_services_table(running_bench_admin_tools)
            bench_info_table.add_row("Bench Admin Tools", bench_admin_table)

        richprint.stdout.print(bench_info_table)

    def shell(self, compose_service: str, user: str | None):
        """
        Spawns a shell for the specified service and user.

        Args:
            service (str): The name of the service.
            user (str | None): The name of the user. If None, defaults to "frappe".

        """
        richprint.change_head("Spawning shell")

        if compose_service == "frappe" and not user:
            user = "frappe"

        if not self.compose_project.is_service_running(compose_service):
            richprint.exit(
                f"Cannot spawn shell. [blue]{self.name}[/blue]'s compose service '{compose_service}' not running!"
            )

        richprint.stop()

        non_bash_supported = ["redis-cache", "redis-socketio", "redis-queue"]

        shell_path = "/bin/bash" if compose_service not in non_bash_supported else "sh"

        exec_args: Dict[str, Any] = {"service": compose_service, "command": shell_path}

        if compose_service == "frappe":
            exec_args["command"] = "/usr/bin/zsh"
            exec_args["workdir"] = "/workspace/frappe-bench"

        if user:
            exec_args["user"] = user

        exec_args["capture_output"] = False

        try:
            self.compose_project.docker.compose.exec(**exec_args)

        except DockerException as e:
            richprint.warning(f"Shell exited with error code: {e.output.exit_code}")

    def get_log_file_paths(self):
        base_log_dir = self.path / "workspace" / "frappe-bench" / "logs"
        if self.bench_config.environment_type == FMBenchEnvType.dev:
            bench_dev_server_log_path = base_log_dir / "web.dev.log"
            return [bench_dev_server_log_path]
        else:
            bench_prod_server_log_path_stdout = base_log_dir / "web.log"
            bench_prod_server_log_path_stderr = base_log_dir / "web.error.log"
            return [bench_prod_server_log_path_stderr, bench_prod_server_log_path_stdout]

    def handle_frappe_server_file_logs(self, follow: bool):
        log_generators = []

        try:
            # Get log file paths
            log_file_paths = self.get_log_file_paths()

            # Check how many log files are available
            num_log_files = len(log_file_paths)

            if num_log_files == 0:
                richprint.print("[yellow]No log files found.[/yellow]")
                return

            # Open log files and create generators
            for path in log_file_paths:
                log_generators.append(log_file(open(path, 'r'), follow=follow))

            if follow:
                while True:
                    try:
                        for line in itertools.chain.from_iterable(log_generators):
                            print(line.strip())
                    except StopIteration:
                        time.sleep(0.1)
            else:
                for lines in itertools.zip_longest(*log_generators, fillvalue=""):
                    for line in lines:
                        if line:
                            print(line.strip())

        finally:
            for logfile in log_generators:
                logfile.close()

    def logs(self, follow: bool, service: Optional[SiteServicesEnum] = None):
        """
        Display logs for the site or a specific service.

        Args:
            follow (bool): Whether to continuously follow the logs or not.
            service (str, optional): The name of the service to display logs for. If not provided, logs for the entire site will be displayed.
        """
        richprint.change_head("Showing logs")
        try:
            if not service:
                self.handle_frappe_server_file_logs(follow=follow)
            else:
                if not self.compose_project.is_service_running(service):
                    richprint.exit(
                        f"Cannot show logs. [blue]{self.name}[/blue]'s compose service '{service}' not running!"
                    )
                self.compose_project.logs(service.value, follow)

        except KeyboardInterrupt:
            richprint.stdout.print("Detected CTRL+C. Exiting..")
            # for log_file in log_files:
            #     log_file.close()

    def attach_to_bench(self, user: str, extensions: List[str], workdir: str, debugger: bool = False):
        """
        Attaches to a running site's container using Visual Studio Code Remote Containers extension.

        Args:
            user (str): The username to be used in the container.
            extensions (List[str]): List of extensions to be installed in the container.
        """

        if not self.compose_project.running:
            raise BenchNotRunning(self.name)

        # check if vscode is installed
        vscode_path = shutil.which("code")

        if not vscode_path:
            # TODO todo this should be exception
            richprint.exit("Visual Studio Code binary i.e 'code' is not accessible via cli.")

        container_hex = self.frappe_container_name_as_hex

        vscode_cmd = shlex.join(
            [
                vscode_path,
                f"--folder-uri=vscode-remote://attached-container+{container_hex}+{workdir}",
            ]
        )

        extensions.sort()

        vscode_config_without_extension = [
            {
                "remoteUser": user,
                "remoteEnv": {"SHELL": "/bin/zsh"},
                "customizations": {
                    "vscode": {
                        "settings": VSCODE_SETTINGS_JSON,
                    }
                },
            }
        ]

        vscode_config_json = copy.deepcopy(vscode_config_without_extension)
        vscode_config_json[0]['customizations']['vscode']["extensions"] = extensions

        labels = {'devcontainer.metadata': json.dumps(vscode_config_json)}

        try:
            labels_previous = self.compose_project.compose_file_manager.get_labels("frappe")[0]
            labels_previous = json.loads(labels_previous["devcontainer.metadata"])
            extensions_previous = copy.deepcopy(labels_previous["customizations"]["vscode"]["extensions"])

        except KeyError:
            extensions_previous = []

        if not extensions_previous == extensions or not user == user:
            richprint.change_head("Configuration changed, regenerating label in bench compose")
            self.compose_project.compose_file_manager.set_labels("frappe", labels)
            self.compose_project.compose_file_manager.write_to_file()
            richprint.print("Regenerated bench compose.")
            self.compose_project.start_service(['frappe'])

        # sync debugger files
        if debugger:
            richprint.change_head("Sync vscode debugger configuration")
            dot_vscode_dir = self.path / "workspace" / "frappe-bench" / ".vscode"
            tasks_json_path = dot_vscode_dir / "tasks"
            launch_json_path = dot_vscode_dir / "launch"
            setting_json_path = dot_vscode_dir / "settings"

            dot_vscode_config = {
                tasks_json_path: VSCODE_TASKS_JSON,
                launch_json_path: VSCODE_LAUNCH_JSON,
                setting_json_path: VSCODE_SETTINGS_JSON,
            }

            if not dot_vscode_dir.exists():
                dot_vscode_dir.mkdir(exist_ok=True, parents=True)

            for file_path in [launch_json_path, tasks_json_path, setting_json_path]:
                file_name = f"{file_path.name}.json"
                real_file_path = file_path.parent / file_name
                if real_file_path.exists():
                    backup_tasks_path = (
                        file_path.parent / f"{file_path.name}.{datetime.now().strftime('%d-%b-%y--%H-%M-%S')}.json"
                    )
                    shutil.copy2(real_file_path, backup_tasks_path)
                    richprint.print(f"Backup previous '{file_name}' : {backup_tasks_path}")

                with open(real_file_path, "w+") as f:
                    f.write(json.dumps(dot_vscode_config[file_path]))

            # install black in env
            try:
                self.compose_project.docker.compose.exec(
                    service="frappe",
                    command="/workspace/frappe-bench/env/bin/pip install black",
                    user='frappe',
                    stream=True,
                )
            except DockerException as e:
                self.logger.error(f"black installation exception: {capture_and_format_exception()}")
                richprint.warning("Not able to install black in env.")

            richprint.print("Synced vscode debugger configuration.")

        richprint.change_head("Attaching to Container")
        output = subprocess.run(vscode_cmd, shell=True)

        if output.returncode != 0:
            raise BenchAttachTocontainerFailed(self.name, 'frappe')

        richprint.print("Attached to frappe service container.")

    def remove_database_and_user(self):
        """
        This function is used to remove db and user of the site at self.name and path at self.path.
        """

        bench_db_info = self.get_db_connection_info()
        richprint.change_head("Removing bench db and db users")
        if "name" in bench_db_info:
            db_name = bench_db_info["name"]
            db_user = bench_db_info["user"]

            if not self.services.database_manager.check_db_exists(db_name):
                richprint.warning(f"Bench db [blue]{db_name}[/blue] not found. Skipping...")
            else:
                self.services.database_manager.remove_db(db_name)
                richprint.print(f"Removed bench db [blue]{db_name}[/blue].")

            if not self.services.database_manager.check_user_exists(db_user):
                richprint.warning(f"Bench db user [blue]{db_user}[/blue] not found. Skipping...")
            else:
                self.services.database_manager.remove_user(db_user, remove_all_host=True)
                richprint.print(f"Removed bench db users [blue]{db_user}[/blue].")

    def remove_bench(self, default_choice: bool = True):
        """
        Removes the site.
        """

        params: Dict[str, Any] = {}
        params['prompt'] = f"🤔 Do you want to remove [bold][green]'{self.name}'[/bold][/green]"
        params['choices'] = ["yes", "no"]

        if default_choice:
            params['default'] = 'no'

        continue_remove = richprint.prompt_ask(**params)

        if continue_remove == "no":
            return False

        richprint.start("Removing bench")

        try:
            self.remove_certificate()
        except Exception as e:
            # self.logger.exception(e)
            richprint.warning(str(e))

        self.remove_database_and_user()
        self.remove_containers_and_dirs()
        return True

    def ensure_workers_running_if_available(self):
        if self.workers.compose_project.compose_file_manager.exists():
            if not self.workers.compose_project.running:
                if self.compose_project.running:
                    self.workers.compose_project.start_service()

    def ensure_admin_tools_running_if_available(self):
        if self.admin_tools.compose_project.compose_file_manager.exists():
            if self.bench_config.admin_tools:
                if not self.admin_tools.compose_project.running:
                    if self.compose_project.running:
                        self.admin_tools.enable()
            else:
                atleast_one_service_running = False

                running_services = self.admin_tools.compose_project.get_services_running_status()
                for service in running_services:
                    if service == 'running':
                        atleast_one_service_running = True

                if atleast_one_service_running:
                    self.admin_tools.disable()

    def sync_admin_tools_compose(self):
        self.admin_tools.generate_compose(self.services.database_manager.database_server_info.host)
        restart_required = self.admin_tools.enable(force_recreate_container=True)
        return restart_required

    def restart_frappe_server(self):
        richprint.change_head("Restarting frappe server")
        restart_command = 'supervisorctl -c /opt/user/supervisord.conf restart all'

        try:
            self.compose_project.docker.compose.exec('frappe', restart_command, user='frappe', stream=False)
        except DockerException as e:
            raise BenchException("frappe", "Faild to restart frappe server.")
        richprint.print("Restarted frappe server.")

    def frappe_service_run_command(self, command: str):
        try:
            self.compose_project.docker.compose.exec('frappe', command, user='frappe', stream=False)
        except DockerException as e:
            raise BenchException("frappe", f"Faild to run {command} in frappe service.")

    def get_apps_dev_requirements(self) -> List[str]:
        """Parse pip requirement string to package name and version"""
        apps_path = self.path / 'workspace' / 'frappe-bench' / 'apps'
        apps_path = apps_path.absolute()

        pattern = '**/pyproject.toml'

        # Find all matching files
        pyproject_files = list(apps_path.glob(pattern))

        import tomlkit

        packages_list = []
        # Print found files
        for pyproject_path in pyproject_files:
            pyproject = tomlkit.parse(pyproject_path.read_text())
            packages = pyproject.get('tool', {}).get('bench', {}).get('dev-dependencies', {})
            for name, version in packages.items():
                full_name = name + version
                packages_list.append(full_name)

        return packages_list

    def remove_dev_packages(self):
        richprint.change_head("Removing dev packages from env.")
        dev_packages = self.get_apps_dev_requirements()
        remove_command = '/workspace/frappe-bench/env/bin/python -m pip uninstall --yes ' + " ".join(dev_packages)
        try:
            self.compose_project.docker.compose.exec('frappe', command=remove_command, user='frappe', stream=False)
        except DockerException as e:
            raise BenchFailedToRemoveDevPackages(self.name)
        richprint.print("Removed dev packages from env.")

    def install_dev_packages(self):
        richprint.change_head("Installing dev packages in env.")
        dev_packages = self.get_apps_dev_requirements()
        install_command = '/workspace/frappe-bench/env/bin/python -m pip install --quiet --upgrade ' + " ".join(
            dev_packages
        )
        try:
            self.compose_project.docker.compose.exec('frappe', command=install_command, user='frappe', stream=False)
        except DockerException as e:
            raise BenchFailedToRemoveDevPackages(self.name)
        richprint.print("Installed dev packages in env.")

    def switch_bench_env(self):
        if not self.is_supervisord_running():
            raise BenchFrappeServiceSupervisorNotRunning(self.name)

        if self.bench_config.environment_type == FMBenchEnvType.dev:
            self.install_dev_packages()
            richprint.change_head(f"Configuring and starting {self.bench_config.environment_type.value} services")
            stop_command = 'supervisorctl -c /opt/user/supervisord.conf stop all'
            self.frappe_service_run_command(stop_command)

            unlink_command = 'rm -rf /opt/user/conf.d/frappe-bench-frappe-web.fm.supervisor.conf'
            self.frappe_service_run_command(unlink_command)

            link_command = 'ln -sfn /opt/user/frappe-dev.conf /opt/user/conf.d/frappe-dev.conf'
            self.frappe_service_run_command(link_command)

            reread_command = 'supervisorctl -c /opt/user/supervisord.conf reread'
            self.frappe_service_run_command(reread_command)

            update_command = 'supervisorctl -c /opt/user/supervisord.conf update'
            self.frappe_service_run_command(update_command)

            start_command = 'supervisorctl -c /opt/user/supervisord.conf start all'
            self.frappe_service_run_command(start_command)

            richprint.print(f"Configured and Started {self.bench_config.environment_type.value} services.")

        elif self.bench_config.environment_type == FMBenchEnvType.prod:
            self.remove_dev_packages()
            richprint.change_head(f"Configuring and starting {self.bench_config.environment_type.value} services")
            stop_command = 'supervisorctl -c /opt/user/supervisord.conf stop all'

            self.frappe_service_run_command(stop_command)

            unlink_command = 'rm -rf /opt/user/conf.d/frappe-dev.conf'
            self.frappe_service_run_command(unlink_command)

            link_command = 'ln -sfn /workspace/frappe-bench/config/frappe-bench-frappe-web.fm.supervisor.conf /opt/user/conf.d/frappe-bench-frappe-web.fm.supervisor.conf'
            self.frappe_service_run_command(link_command)

            reread_command = 'supervisorctl -c /opt/user/supervisord.conf reread'
            self.frappe_service_run_command(reread_command)

            update_command = 'supervisorctl -c /opt/user/supervisord.conf update'
            self.frappe_service_run_command(update_command)

            start_command = 'supervisorctl -c /opt/user/supervisord.conf start all'
            self.frappe_service_run_command(start_command)

            richprint.print(f"Configured and Started {self.bench_config.environment_type.value} services.")

    def is_supervisord_running(self, interval: int = 2, timeout: int = 30):
        for i in range(timeout):
            try:
                status_command = 'supervisorctl -c /opt/user/supervisord.conf status all'
                output = self.compose_project.docker.compose.exec('frappe', status_command, user='frappe', stream=False)
                return True
            except DockerException as e:
                if any('frappe-bench' in s for s in e.output.combined):
                    return True
                time.sleep(interval)
                continue
        return False
