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
from rich.table import Table
from frappe_manager.docker import DockerException
from frappe_manager.docker import ComposeFile, DockerClient
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.logger import log
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager import VSCODE_LAUNCH_JSON, VSCODE_SETTINGS_JSON, VSCODE_TASKS_JSON
from frappe_manager.site_manager.modules.bench_admin_tools import BenchAdminTools
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.site_manager.exceptions import (
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
from frappe_manager.site_manager.modules.bench_workers import BenchWorkers
from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps
from frappe_manager.site_manager.modules.bench_supervisor import BenchSupervisor
from frappe_manager.site_manager.modules.bench_ssl import BenchSSL
from frappe_manager.site_manager.modules.bench_devtools import BenchDevTools
from frappe_manager.site_manager.modules.bench_database import BenchDatabase
from frappe_manager.site_manager.modules.bench_info import BenchInfo
from frappe_manager.site_manager.modules.bench_workers import BenchWorkerCoordinator
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager
from frappe_manager.site_manager.modules.bench_app import BenchAppManager
from frappe_manager.site_manager.modules.bench_orchestrator import BenchOrchestrator
from frappe_manager.site_manager.modules.upload_limit_manager import UploadLimitManager
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.proxy_storage import ProxyStoragePaths
from frappe_manager.ssl_manager.nginx_controller import NginxController
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.service_factory import create_certificate_service
from frappe_manager.utils.helpers import (
    capture_and_format_exception,
    format_ssl_certificate_time_remaining,
    get_current_fm_version,
    log_file,
    save_dict_to_file,
)
from frappe_manager.utils.docker import host_run_cp
from frappe_manager import (
    STABLE_APP_BRANCH_MAPPING_LIST,
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    CLI_DIR,
    SiteServicesEnum,
)
from frappe_manager.utils.site import domain_level, generate_services_table


class Bench:
    def __init__(
        self,
        path: Path,
        name: str,
        bench_config: BenchConfig,
        compose_file_manager: ComposeFile,
        docker_client: DockerClient,
        services: ServicesManager,
        workers_check: bool = True,
        admin_tools_check: bool = True,
        verbose: bool = False,
        output_handler: OutputHandler | None = None,
    ) -> None:
        self.path = path
        self.name = name
        self.quiet = not verbose
        self.output = output_handler or RichOutputHandler()
        self.services = services
        self.backup_path = self.path / 'backups'
        self.bench_config: BenchConfig = bench_config
        self.logger = log.get_logger()

        # Store compose_file_manager and docker_client directly
        self.compose_file_manager = compose_file_manager
        self.docker_client = docker_client

        # Initialize specialized modules
        self.docker_ops = BenchDockerOps(
            docker_client=docker_client,
            compose_file_manager=compose_file_manager,
            config=bench_config,
            path=path,
            quiet=self.quiet,
            output_handler=self.output,
        )
        self.supervisor = BenchSupervisor(
            docker_client=docker_client,
            config=bench_config,
            bench_name=name,
            output_handler=self.output,
        )

        # Initialize local nginx proxy components
        self.bench_proxy_storage = ProxyStoragePaths('nginx', self.compose_file_manager)
        self.bench_nginx_controller = NginxController('nginx', self.compose_file_manager, self.docker_client)

        # For backward compatibility with admin_tools
        # Create a simple proxy manager object with required attributes
        self.proxy_manager = type(
            'ProxyManager',
            (),
            {
                'dirs': self.bench_proxy_storage.dirs,
                'restart': self.bench_nginx_controller.restart,
                'reload': self.bench_nginx_controller.reload,
            },
        )()

        self.admin_tools = BenchAdminTools(self, self.proxy_manager, verbose=verbose, output_handler=self.output)

        # Initialize SSL certificate manager with dependency injection
        # Get global nginx-proxy storage config from services
        global_proxy_storage = services.proxy_storage
        webroot_dir = self.bench_proxy_storage.dirs.html.host

        ssl_storage_config = SSLStorageConfig(
            ssl_dir=global_proxy_storage.dirs.ssl.host,
            ssl_dir_container=global_proxy_storage.dirs.ssl.container,
            certs_dir=global_proxy_storage.dirs.certs.host,
            certs_dir_container=global_proxy_storage.dirs.certs.container,
            vhostd_dir=global_proxy_storage.dirs.vhostd.host,
            webroot_dir=webroot_dir,
        )

        # Create link manager
        link_manager = CertificateLinkManager(ssl_storage_config)

        # Initialize multi-certificate manager with service factory
        # The factory will create appropriate certificate services (acme.sh) for each certificate
        def certificate_service_factory(cert, storage_cfg, output_handler):
            return create_certificate_service(cert, storage_cfg, output_handler)

        self.certificate_manager = SSLCertificateManager(
            certificates=self.bench_config.ssl_certificates,
            service_factory=certificate_service_factory,
            link_manager=link_manager,
            nginx_controller=services.nginx_controller,
            storage_config=ssl_storage_config,
            config_save_callback=self.save_bench_config,
            output_handler=self.output,
        )

        # Initialize SSL module
        self.ssl = BenchSSL(
            certificate_manager=self.certificate_manager,
            bench_name=name,
            is_service_running_fn=self._is_service_running,
        )

        # Initialize DevTools module
        self.devtools = BenchDevTools(
            docker_client=docker_client,
            compose_file_manager=compose_file_manager,
            bench_path=path,
            bench_name=name,
            is_running_fn=lambda: self.running,
            switch_bench_env_fn=self.switch_bench_env,
            quiet=self.quiet,
            output_handler=self.output,
        )
        self.devtools.logger = self.logger

        # Initialize Database module
        self.database = BenchDatabase(
            bench_name=name,
            bench_path=path,
            services=services,
            set_common_bench_config_fn=self.set_common_bench_config,
            output_handler=self.output,
        )

        # Initialize Site Manager module
        self.site_manager = BenchSiteManager(
            bench_name=name,
            bench_path=path,
            docker_client=docker_client,
            bench_config=bench_config,
            services=services,
            quiet=self.quiet,
            output_handler=self.output,
        )

        # Initialize App Manager module
        self.app_manager = BenchAppManager(
            bench_name=name,
            bench_path=path,
            docker_client=docker_client,
            bench_config=bench_config,
            quiet=self.quiet,
            output_handler=self.output,
        )

        self.workers = BenchWorkers(self, not verbose, output_handler=self.output)

        # Initialize Info module
        self.info_display = BenchInfo(
            bench_name=name,
            bench_path=path,
            bench_config=bench_config,
            services=services,
            workers=self.workers,
            admin_tools=self.admin_tools,
            certificate_manager=self.certificate_manager,
            get_db_connection_info_fn=self.get_db_connection_info,
            has_certificate_fn=lambda: self.has_certificate(),
            is_running_fn=lambda: self.running,
            get_services_running_status_fn=self._get_services_running_status,
            output_handler=self.output,
        )

        # Initialize WorkerCoordinator module
        self.worker_coordinator = BenchWorkerCoordinator(
            bench_name=name,
            workers=self.workers,
            supervisor=self.supervisor,
            bench_path=self.path,
            restart_supervisor_service_fn=self.restart_supervisor_service,
            is_running_fn=lambda: self.running,
            quiet=self.quiet,
            output_handler=self.output,
        )

        # Initialize Orchestrator for complex workflows
        self.orchestrator = BenchOrchestrator(self, output_handler=self.output)

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
        output_handler: OutputHandler | None = None,
    ) -> 'Bench':
        if domain_level(bench_name) == 0:
            bench_name = bench_name + ".localhost"

        bench_path = benches_path / bench_name
        bench_config_path: Path = bench_path / bench_config_file_name

        compose_file_manager = ComposeFile(bench_path / "docker-compose.yml")
        docker_client = DockerClient(compose_file_path=bench_path / "docker-compose.yml")

        bench_config: BenchConfig = BenchConfig.import_from_toml(bench_config_path)

        parms: Dict[str, Any] = {
            'name': bench_name,
            'path': bench_path,
            'bench_config': bench_config,
            'compose_file_manager': compose_file_manager,
            'docker_client': docker_client,
            'services': services,
            'workers_check': workers_check,
            'admin_tools_check': admin_tools_check,
        }

        # Add output_handler if provided
        if output_handler is not None:
            parms['output_handler'] = output_handler

        return cls(**parms)

    def _is_service_running(self, service: str) -> bool:
        """Check if a specific service is running."""
        return self.docker_ops._is_service_running(service)

    @property
    def running(self) -> bool:
        """Check if all bench services are running."""
        return self.docker_ops.is_running()

    def _get_services_running_status(self) -> dict:
        """Get the running status of all services."""
        return self.docker_ops.get_services_running_status()

    def sync_bench_config_configuration(self):
        # set developer_mode based on config
        self.set_common_bench_config({'developer_mode': self.bench_config.developer_mode})

        # ssl
        certificate_updated = self.update_certificate(self.bench_config.get_primary_certificate(), raise_error=False)
        if certificate_updated:
            self.output.print("Certificate Updated.")

        # admin tools
        if self.bench_config.admin_tools:
            if not self.admin_tools.compose_file_manager.compose_path.exists():
                self.sync_admin_tools_compose()
            else:
                self.admin_tools.enable(force_configure=True)
            self.output.print("Enabled Admin-tools.")

        else:
            if not self.admin_tools.compose_file_manager.compose_path.exists():
                self.output.print("Admin tools is already disabled.")
            else:
                self.admin_tools.disable()
                self.output.print("Disabled Admin-tools.")

        self.output.change_head("Restarting frappe server")
        self.restart_supervisor_service('frappe')
        self.output.print("Restarted frappe server")

    def save_bench_config(self):
        self.output.change_head("Saving bench config changes")
        self.bench_config.export_to_toml(self.bench_config.root_path)
        self.output.print("Saved bench config.")

    @property
    def exists(self):
        return self.path.exists()

    def create(self, is_template_bench: bool = False):
        """
        Creates a new bench using the provided template inputs.

        Args:
            is_template_bench: If True, creates a minimal bench without full site setup

        Returns:
            None
        """
        self.orchestrator.create_bench(is_template_bench)

    def set_common_bench_config(self, config: dict):
        """
        Sets the values in the common_site_config.json file.

        Args:
            config (dict): A dictionary containing the key-value pairs
        """
        common_bench_config_path = self.path / "workspace/frappe-bench/sites/common_site_config.json"
        if not common_bench_config_path.exists():
            raise BenchException(self.name, message=f'File not found {common_bench_config_path.name}.')

        save_dict_to_file(config, common_bench_config_path)

    def set_bench_site_config(self, config: dict):
        """
        Sets the values in the bench's site site_config.json file.

        Args:
            config (dict): A dictionary containing the key-value pairs
        """
        site_config_path = self.path / "workspace/frappe-bench/sites" / self.name / "site_config.json"
        if not site_config_path.exists():
            raise BenchException(self.name, message=f'File not found {site_config_path.name}.')
        save_dict_to_file(config, site_config_path)

    def get_common_bench_config(self):
        return self.info_display.get_common_config()

    def get_bench_site_config(self):
        return self.info_display.get_site_config()

    def generate_compose(self, inputs: dict) -> None:
        """
        Generates the compose file for the site based on the given inputs.

        Args:
            inputs (dict): A dictionary containing the inputs for generating the compose file.

        Returns:
            None
        """
        return self.docker_ops.generate_compose(inputs)

    def sync_bench_common_site_config(self, services_db_host: str, services_db_port: int):
        """
        Syncs the common site configuration with the global database information and container prefix.

        This function sets the common site configuration data including the socketio port, database host and port,
        and the Redis cache, queue, and socketio URLs.
        """
        self.database.sync_common_site_config(services_db_host, services_db_port)

    def create_compose_dirs(self) -> bool:
        """
        Creates the necessary directories for the Compose setup.

        Returns:
            bool: True if the directories are created successfully, False otherwise.
        """
        return self.docker_ops.create_compose_dirs()

        return True

    def start(
        self,
        force: bool = False,
        sync_bench_config_changes: bool = False,
        reconfigure_workers: bool = False,
        include_default_workers=False,
        include_custom_workers=False,
        reconfigure_supervisor: bool = False,
        reconfigure_common_site_config: bool = False,
        sync_dev_packages: bool = False,
    ):
        """
        Starts the bench with various configuration options.
        """
        self.orchestrator.start_bench(
            force=force,
            sync_bench_config_changes=sync_bench_config_changes,
            reconfigure_workers=reconfigure_workers,
            include_default_workers=include_default_workers,
            include_custom_workers=include_custom_workers,
            reconfigure_supervisor=reconfigure_supervisor,
            reconfigure_common_site_config=reconfigure_common_site_config,
            sync_dev_packages=sync_dev_packages,
        )

    def frappe_logs_till_start(self):
        """
        Retrieves and prints the logs of the 'frappe' service until site supervisor starts.

        Args:
            status_msg (str, optional): Custom status message to display. Defaults to None.
        """
        return self.docker_ops.frappe_logs_till_start()

    def stop(self):
        """
        Stop the site by stopping the containers.

        Returns:
            bool: True if the site is successfully stopped, False otherwise.
        """
        self.output.change_head("Stopping bench services")
        self.docker_ops.stop(timeout=10)
        self.output.print("Stopped bench services.")

        if self.workers.compose_file_manager.exists():
            self.output.change_head("Starting bench workers services")
            output = self.workers.docker_client.compose.stop(services=[], timeout=10, stream=self.quiet)
            if self.quiet:
                self.output.live_lines(output, padding=(0, 0, 0, 2))
            self.output.print("Started bench workers services")

        # stop admin_tools if exists
        if self.admin_tools.compose_file_manager.exists():
            self.output.change_head("Stopped bench admin tools services")
            self.admin_tools.disable()
            self.output.print("Stopped bench admin tools services.")

    def remove_containers_and_dirs(self):
        """
        Removes the site by stopping and removing the containers associated with it,
        and deleting the site directory.

        Returns:
            bool: True if the site is successfully removed, False otherwise.
        """
        # TODO handle low level errors like read only, write only, etc.
        if self.compose_file_manager.exists():
            self.output.change_head("Removing bench containers.")
            self.docker_ops.remove_containers(remove_volumes=True, timeout=5)
            self.output.print("Removed bench containers.")
        else:
            self.output.warning('Bench compose file not found. Skipping containers removal.')

        if self.workers.compose_file_manager.exists():
            self.output.change_head("Removing bench workers containers.")
            output = self.workers.docker_client.compose.down(remove_orphans=True, volumes=True, timeout=5, stream=True)
            self.output.live_lines(output, padding=(0, 0, 0, 2))
            self.output.print("Removed bench workers containers.")
        else:
            self.output.warning('Bench workers compose file not found. Skipping containers removal.')

        if self.admin_tools.compose_file_manager.exists():
            self.output.change_head("Removing bench admin tools containers.")
            # down_service equivalent: stop + remove containers + volumes
            try:
                self.admin_tools.docker_client.compose.down(remove_orphans=True, volumes=True, timeout=5, stream=True)
            except Exception:
                pass  # Best effort cleanup
            self.output.print("Removed bench admin tools containers.")
        else:
            self.output.warning('Bench admin tools compose file not found. Skipping containers removal.')

        self.output.change_head("Removing all bench files and directories.")
        try:
            shutil.rmtree(self.path)
        except PermissionError:
            try:
                images = self.compose_file_manager.get_all_images()
                if "frappe" in images:
                    frappe_image = images["frappe"]
                    frappe_image = f"{frappe_image['name']}:{frappe_image['tag']}"
                    self.docker_client.run(
                        image=frappe_image,
                        entrypoint="/bin/sh",
                        command="-c 'chown -R frappe:frappe .'",
                        volume=[f"{self.path}/workspace:/workspace"],
                        stream=False,
                    )
                    shutil.rmtree(self.path)
            except Exception:
                raise BenchRemoveDirectoryError(self.name, self.path)

        self.output.print("Removed all bench files and directories.")

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
                result = self.docker_client.compose.exec(
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

    def sync_workers_compose(
        self,
        force_recreate: bool = False,
        setup_supervisor: bool = True,
        include_default_workers: bool = True,
        include_custom_workers: bool = True,
    ):
        self.worker_coordinator.sync_workers_compose(
            force_recreate=force_recreate,
            setup_supervisor=setup_supervisor,
            include_default_workers=include_default_workers,
            include_custom_workers=include_custom_workers,
        )

    def backup_restore_workers_supervisor(self, backup_manager: BackupManager):
        self.worker_coordinator.backup_restore_workers_supervisor(backup_manager)

    def backup_workers_supervisor_conf(self):
        return self.worker_coordinator.backup_workers_supervisor_conf()

    def regenerate_workers_supervisor_conf(self):
        self.worker_coordinator.regenerate_workers_supervisor_conf()

    def get_bench_installed_apps_list(self):
        return self.info_display.get_installed_apps_list()

    # this can be plugable
    def get_db_connection_info(self):
        return self.database.get_connection_info()

    def create_certificate(self):
        self.ssl.create_individual_certificates()
        self.save_bench_config()

    def has_certificate(self):
        return self.ssl.has_certificate()

    def remove_certificate(self):
        """
        Remove ALL SSL certificates for this bench.

        This removes certificates for the primary domain and all alias domains,
        including their symlinks, vhost configs, and acme.sh configurations.
        Then clears the certificate list in bench_config.
        """
        self.ssl.remove_all_certificates()
        # Clear all certificates from config
        self.bench_config.ssl_certificates = []
        self.save_bench_config()

    def update_certificate(self, certificate: SSLCertificate, raise_error: bool = True):
        result = self.ssl.update_certificate(certificate, raise_error)
        if result:
            self.bench_config.set_primary_certificate(certificate)
        return result

    def renew_certificate(self):
        return self.ssl.renew_certificate()

    def update_alias_domains(self, add_domains: Optional[List[str]] = None, remove_domains: Optional[List[str]] = None):
        """
        Update alias domains for the bench with atomic rollback support.

        Works independently of SSL status:
        - If SSL is active: regenerates certificate with updated domains
        - If SSL is inactive: updates config only

        Args:
            add_domains: List of domains to add as aliases
            remove_domains: List of domains to remove from aliases

        Raises:
            ValueError: If attempting to remove primary domain
            Exception: If certificate generation fails (config is rolled back)
        """
        self.orchestrator.update_alias_domains(add_domains, remove_domains)

    def info(self):
        """
        Retrieves and displays information about the bench.

        This method retrieves various information about the site, such as site URL, site root, database details,
        Frappe username and password, root database user and password, and more. It then formats and displays
        this information using the richprint library.
        """
        self.info_display.display_info()

    def shell(self, compose_service: str, user: str | None):
        """
        Spawns a shell for the specified service and user.

        Args:
            service (str): The name of the service.
            user (str | None): The name of the user. If None, defaults to "frappe".

        """
        return self.docker_ops.shell(compose_service, user)

    def execute_command(self, compose_service: str, command: str, user: str | None = None) -> int:
        """
        Execute a single command in the specified service and return exit code.

        Args:
            compose_service: The name of the service
            command: The command to execute
            user: The name of the user (defaults to "frappe" for frappe service)

        Returns:
            Exit code of the executed command
        """
        return self.docker_ops.execute_command(compose_service, command, user)

    def get_log_file_paths(self):
        return self.info_display.get_log_file_paths()

    def handle_frappe_server_file_logs(self, follow: bool):
        log_generators = []

        try:
            # Get log file paths
            log_file_paths = self.get_log_file_paths()

            # Check how many log files are available
            num_log_files = len(log_file_paths)

            if num_log_files == 0:
                self.output.print("[yellow]No log files found.[/yellow]")
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
        self.output.change_head("Showing logs")
        try:
            if not service:
                self.handle_frappe_server_file_logs(follow=follow)
            else:
                if not self._is_service_running(service):
                    self.output.stop()
                    self.output.display_error(
                        f"Cannot show logs. [blue]{self.name}[/blue]'s compose service '{service}' not running!"
                    )
                    return
                self.docker_ops.logs(services=[service.value], follow=follow)

        except KeyboardInterrupt:
            print("Detected CTRL+C. Exiting..")

    def attach_to_bench(self, user: str, extensions: List[str], workdir: str, debugger: bool = False) -> None:
        """
        Attaches to a running bench's container using Visual Studio Code Remote Containers extension.

        Args:
            user: Username to be used in the container
            extensions: List of VS Code extensions to install
            workdir: Working directory path inside container
            debugger: Whether to setup debugging configuration

        Raises:
            BenchNotRunning: If the bench container is not running
            BenchAttachTocontainerFailed: If attaching to container fails
        """
        return self.devtools.attach_to_bench(user, extensions, workdir, debugger)

    def remove_database_and_user(self):
        """
        This function is used to remove db and user of the site at self.name and path at self.path.
        """
        self.database.remove_database_and_user()

    def remove_bench(self, default_choice: bool = True, delete_db_from_global_db: bool | None = None):
        """
        Removes the bench.

        Args:
            default_choice: If True, defaults to 'no' for confirmation prompt
            delete_db_from_global_db: Whether to delete DB from global-db.
                                     If None, prompts interactively when DB is in global-db.
        """

        params: Dict[str, Any] = {}
        params['prompt'] = f"🤔 Do you want to remove [bold][green]'{self.name}'[/bold][/green]"
        params['choices'] = ["yes", "no"]

        if default_choice:
            params['default'] = 'no'

        continue_remove = self.output.prompt_ask(**params)

        if continue_remove == "no":
            return False

        self.output.start("Removing bench")

        try:
            self.remove_certificate()
        except Exception as e:
            # self.logger.exception(e)
            self.output.warning(str(e))

        # Handle database deletion based on configuration
        self._handle_database_deletion(delete_db_from_global_db)

        self.remove_containers_and_dirs()
        return True

    def _is_using_global_db(self) -> bool:
        """
        Check if bench is using FM's managed global-db service.

        Returns:
            True if bench uses global-db, False otherwise
        """
        try:
            db_info = self.database.get_connection_info()
            db_host = db_info.get("host", "")

            # Check if the database host is global-db
            # FM's global-db service is accessed via the container name "global-db"
            return db_host == "global-db"
        except Exception:
            # If we can't determine, assume it's not global-db
            return False

    def _handle_database_deletion(self, delete_db_from_global_db: bool | None):
        """
        Handle database deletion based on user preference and database location.

        Args:
            delete_db_from_global_db: User preference for database deletion.
                                     None = prompt if using global-db
                                     True = delete from global-db
                                     False = don't delete from global-db
        """
        is_global_db = self._is_using_global_db()

        # If not using global-db, always skip database deletion
        if not is_global_db:
            self.output.print("Bench is not using FM's managed global-db. Skipping database deletion.")
            return

        # If using global-db, determine whether to delete
        should_delete = delete_db_from_global_db

        # If not specified, prompt the user
        if should_delete is None:
            params = {
                'prompt': f"🗄️  Do you want to remove the database '[bold]{self.name}[/bold]' from global-db?",
                'choices': ["yes", "no"],
                'default': 'yes',
            }
            choice = self.output.prompt_ask(**params)
            should_delete = choice == "yes"

        # Perform deletion if requested
        if should_delete:
            self.remove_database_and_user()
        else:
            self.output.print("Skipping database deletion from global-db.")

    def ensure_workers_running_if_available(self):
        self.worker_coordinator.ensure_workers_running_if_available()

    def ensure_admin_tools_running_if_available(self):
        if self.admin_tools.compose_file_manager.exists():
            if self.bench_config.admin_tools:
                # Check if admin tools is running
                admin_tools_running = False
                try:
                    services = self.admin_tools.compose_file_manager.get_services_list()
                    containers = self.admin_tools.compose_file_manager.get_container_names().values()
                    all_statuses = self.admin_tools.docker_client.compose.get_all_services_status()
                    running_statuses = {
                        status["Service"]: status["State"]
                        for status in all_statuses
                        if status.get("Name") in containers
                    }
                    admin_tools_running = all(running_statuses.get(service) == "running" for service in services)
                except Exception:
                    admin_tools_running = False

                if not admin_tools_running:
                    if self.running:
                        self.admin_tools.enable()
            else:
                atleast_one_service_running = False

                # Get admin tools running services
                try:
                    services = self.admin_tools.compose_file_manager.get_services_list()
                    containers = self.admin_tools.compose_file_manager.get_container_names().values()
                    all_statuses = self.admin_tools.docker_client.compose.get_all_services_status()
                    running_services = {
                        status["Service"]: status["State"]
                        for status in all_statuses
                        if status.get("Name") in containers
                    }
                    for service in running_services:
                        if service == 'running':
                            atleast_one_service_running = True
                except Exception:
                    atleast_one_service_running = False

                if atleast_one_service_running:
                    self.admin_tools.disable()

    def sync_admin_tools_compose(self):
        self.admin_tools.generate_compose(self.services.database_manager.database_server_info.host)
        restart_required = self.admin_tools.enable(force_recreate_container=True)
        return restart_required

    def frappe_service_run_command(self, command: str):
        try:
            self.docker_client.compose.exec('frappe', command, user='frappe', stream=False)
        except DockerException as e:
            raise BenchException("frappe", f"Faild to run {command} in frappe service.")

    def get_apps_dev_requirements(self) -> List[str]:
        """Parse pip requirement string to package name and version"""
        return self.devtools.get_apps_dev_requirements()

    def remove_dev_packages(self):
        return self.devtools.remove_dev_packages()

    def install_dev_packages(self):
        return self.devtools.install_dev_packages()

    def switch_bench_env(self, timeout: int = 30, interval: int = 1):
        return self.supervisor.switch_bench_env('frappe', timeout, interval)

    def is_supervisord_running(self, interval: int = 2, timeout: int = 30):
        return self.supervisor.is_supervisord_running(interval, timeout)

    def reset(self, admin_password: Optional[str] = None):
        admin_pass = None

        if admin_password:
            admin_pass = admin_password
        else:
            if not admin_pass:
                site_config = self.get_bench_site_config()
                if 'admin_password' in site_config:
                    admin_pass = site_config['admin_password']
                    self.output.print("Using admin_password defined in site_config.json")

            if not admin_pass:
                common_site_config = self.get_common_bench_config()
                if 'admin_password' in common_site_config:
                    admin_pass = common_site_config['admin_password']
                    self.output.print("Using admin_password defined in common_site_config.json")

        if not admin_pass:
            admin_pass = self.output.prompt_ask(prompt=f"Please enter admin password for site {self.name}")

        self.output.change_head(f"Resetting bench site {self.name}")

        self.site_manager.reset_bench_site(admin_pass)
        self.set_bench_site_config({'admin_password': admin_pass})

        self.output.print(f"Reset bench site {self.name}")

    def restart_supervisor_service(
        self, service: str, docker_client_obj: Optional['DockerClient'] = None, timeout: int = 30, interval: int = 1
    ):
        return self.supervisor.restart_supervisor_service(service, docker_client_obj, timeout, interval)

    def restart_web_containers_services(self):
        """Restarts frappe server and socketio containers"""

        # restart frappe server and socketio
        web_services = [
            SiteServicesEnum.frappe.value,
            SiteServicesEnum.socketio.value,
        ]

        for service in web_services:
            self.output.change_head(f"Restarting web services - {service}")
            is_restarted = self.restart_supervisor_service(service)
            if is_restarted:
                self.output.print(f"Restarted web services - {service}")

    def restart_redis_services_containers(self):
        """Restarts redis containers"""

        redis_services = [
            SiteServicesEnum.redis_cache.value,
            SiteServicesEnum.redis_queue.value,
        ]
        self.output.change_head(f"Restarting redis services - {' '.join(redis_services)}")
        self.docker_ops.restart_services(redis_services)
        self.output.print(f"Restarted redis services - {' '.join(redis_services)}")

    def restart_workers_containers_services(self):
        """Restarts workers and schedule containers"""
        self.worker_coordinator.restart_workers_containers_services()

    def update_upload_limit(self, upload_limit: str):
        """
        Update upload size limit across all three required locations:
        1. site_config.json (max_file_size in bytes)
        2. Bench nginx config (via template regeneration)
        3. nginx-proxy vhost.d files (for all domains)

        Args:
            upload_limit: Size string (e.g., "50M", "100M", "1G")

        Raises:
            BenchException: If format is invalid or operation fails
        """
        from frappe_manager.site_manager.modules.upload_limit_manager import UploadLimitManager
        import re

        # Validate format (e.g., "50M", "100M", "500M", "1G")
        if not re.match(r'^\d+[MG]$', upload_limit, re.IGNORECASE):
            raise BenchException(
                self.name, message=f"Invalid upload limit format: '{upload_limit}'. Use format like '50M' or '1G'"
            )

        # 1. Update site_config.json (convert to bytes)
        size_bytes = self._parse_size_to_bytes(upload_limit)
        self.set_bench_site_config({'max_file_size': size_bytes})
        self.output.print(f"Updated site_config.json (max_file_size: {size_bytes} bytes)")

        # 2. Update BenchConfig (will affect nginx template on restart)
        self.bench_config.upload_limit = upload_limit.upper()
        self.save_bench_config()
        self.output.print(f"Updated bench configuration")

        # 2b. Regenerate docker-compose to include new environment variable
        inputs = self.bench_config.export_to_compose_inputs()
        self.generate_compose(inputs)
        self.output.print(f"Regenerated docker-compose configuration")

        # 3. Create custom nginx config file for bench nginx
        custom_conf_dir = self.path / "configs" / "nginx" / "conf" / "custom"
        custom_conf_dir.mkdir(parents=True, exist_ok=True)
        upload_limit_conf = custom_conf_dir / "upload-limit.conf"
        upload_limit_conf.write_text(f"client_max_body_size {upload_limit.lower()};\n")
        self.output.print("Created custom nginx configuration")

        # 4. Reload bench nginx to apply configuration
        self.bench_nginx_controller.reload()

        # 5. Update nginx-proxy vhost.d for all domains (primary + aliases)
        all_domains = [self.name] + self.bench_config.alias_domains
        vhostd_dir = self.services.path / "nginx-proxy" / "vhostd"

        if vhostd_dir.exists():
            upload_mgr = UploadLimitManager(vhostd_dir)
            upload_mgr.set_upload_limit_for_domains(all_domains, upload_limit.lower())
            self.output.print(f"Updated nginx-proxy vhost.d for {len(all_domains)} domain(s)")

        # 6. Reload nginx-proxy to pick up vhost.d changes
        if self.services.is_service_running("global-nginx-proxy"):
            self.services.nginx_controller.reload()

        self.output.print(
            f"Upload size limit updated to {upload_limit} (site_config: {size_bytes} bytes, nginx: {upload_limit.lower()})"
        )

    def _parse_size_to_bytes(self, size_str: str) -> int:
        """
        Convert size string (e.g., '50M', '1G') to bytes for Frappe site_config.json.

        Args:
            size_str: Size string (e.g., "50M", "1G")

        Returns:
            Size in bytes (integer)

        Raises:
            BenchException: If format is invalid

        Examples:
            "50M" -> 52428800 (50 * 1024 * 1024)
            "1G"  -> 1073741824 (1 * 1024 * 1024 * 1024)
        """
        import re

        match = re.match(r'^(\d+)([MG])$', size_str, re.IGNORECASE)
        if not match:
            raise BenchException(
                self.name,
                message=f"Invalid size format: '{size_str}'. Expected format: <number><unit> (e.g., '50M', '1G')",
            )

        value = int(match.group(1))
        unit = match.group(2).upper()

        if unit == 'M':
            return value * 1024 * 1024  # Convert MB to bytes
        elif unit == 'G':
            return value * 1024 * 1024 * 1024  # Convert GB to bytes

        # Should never reach here due to regex validation
        raise BenchException(self.name, message=f"Unsupported unit: {unit}")
