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
from frappe_manager.docker import DockerException
from frappe_manager.docker import ComposeFile, DockerClient
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
from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps
from frappe_manager.site_manager.modules.bench_supervisor import BenchSupervisor
from frappe_manager.site_manager.modules.bench_ssl import BenchSSL
from frappe_manager.site_manager.modules.bench_devtools import BenchDevTools
from frappe_manager.site_manager.modules.bench_database import BenchDatabase
from frappe_manager.site_manager.modules.bench_info import BenchInfo
from frappe_manager.site_manager.modules.bench_workers import BenchWorkerCoordinator
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.proxy_storage import ProxyStoragePaths
from frappe_manager.ssl_manager.nginx_controller import NginxController
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.letsencrypt_certificate_service import LetsEncryptCertificateService
from frappe_manager.ssl_manager.no_op_certificate_service import NoOpCertificateService
from frappe_manager.utils.helpers import (
    capture_and_format_exception,
    format_ssl_certificate_time_remaining,
    get_current_fm_version,
    log_file,
    save_dict_to_file,
)
from frappe_manager.utils.docker import host_run_cp
from frappe_manager import (
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
    ) -> None:
        self.path = path
        self.name = name
        self.quiet = not verbose
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
            quiet=self.quiet
        )
        self.supervisor = BenchSupervisor(
            docker_client=docker_client,
            config=bench_config,
            bench_name=name
        )
        
        # Initialize local nginx proxy components
        self.bench_proxy_storage = ProxyStoragePaths('nginx', self.compose_file_manager)
        self.bench_nginx_controller = NginxController('nginx', self.compose_file_manager, self.docker_client)
        
        # For backward compatibility with admin_tools
        # Create a simple proxy manager object with required attributes
        self.proxy_manager = type('ProxyManager', (), {
            'dirs': self.bench_proxy_storage.dirs,
            'restart': self.bench_nginx_controller.restart,
            'reload': self.bench_nginx_controller.reload,
        })()
        
        self.admin_tools: AdminTools = AdminTools(self, self.proxy_manager)

        # Initialize SSL certificate manager with dependency injection
        # Get global nginx-proxy storage config from services
        global_proxy_storage = services.proxy_storage
        webroot_dir = self.bench_proxy_storage.dirs.html.host
        
        ssl_storage_config = SSLStorageConfig(
            ssl_dir=global_proxy_storage.dirs.ssl.host,
            ssl_dir_container=global_proxy_storage.dirs.ssl.container,
            certs_dir=global_proxy_storage.dirs.certs.host,
            certs_dir_container=global_proxy_storage.dirs.certs.container,
            webroot_dir=webroot_dir,
        )
        
        # Create certificate service based on SSL type
        if self.bench_config.ssl.ssl_type == SUPPORTED_SSL_TYPES.le:
            certificate_service = LetsEncryptCertificateService(
                ssl_storage_config.ssl_dir,
                webroot_dir
            )
        else:
            certificate_service = NoOpCertificateService(Path('/dev/null'))
        
        # Create link manager and nginx controller
        link_manager = CertificateLinkManager(ssl_storage_config)
        
        # Initialize certificate manager
        self.certificate_manager = SSLCertificateManager(
            certificate=self.bench_config.ssl,
            service=certificate_service,
            link_manager=link_manager,
            nginx_controller=services.nginx_controller,
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
        )
        self.devtools.logger = self.logger
        
        # Initialize Database module
        self.database = BenchDatabase(
            bench_name=name,
            bench_path=path,
            services=services,
            set_common_bench_config_fn=self.set_common_bench_config,
        )
        
        self.benchops = BenchOperations(self)
        self.workers = BenchWorkers(self, not verbose)
        
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
        )
        
        # Initialize WorkerCoordinator module
        self.worker_coordinator = BenchWorkerCoordinator(
            bench_name=name,
            workers=self.workers,
            benchops=self.benchops,
            restart_supervisor_service_fn=self.restart_supervisor_service,
            is_running_fn=lambda: self.running,
            quiet=self.quiet,
        )

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
        certificate_updated = self.update_certificate(self.bench_config.ssl, raise_error=False)
        if certificate_updated:
            richprint.print("Certificate Updated.")

        # admin tools
        if self.bench_config.admin_tools:
            if not self.admin_tools.compose_file_manager.compose_path.exists():
                self.sync_admin_tools_compose()
            else:
                self.admin_tools.enable(force_configure=True)
            richprint.print("Enabled Admin-tools.")

        else:
            if not self.admin_tools.compose_file_manager.compose_path.exists():
                richprint.print("Admin tools is already disabled.")
            else:
                self.admin_tools.disable()
                richprint.print("Disabled Admin-tools.")

        richprint.change_head("Restarting frappe server")
        self.restart_supervisor_service('frappe')
        richprint.print("Restarted frappe server")

    def save_bench_config(self):
        richprint.change_head("Saving bench config changes")
        self.bench_config.export_to_toml(self.bench_config.root_path)
        richprint.print("Saved bench config.")

    @property
    def exists(self):
        return self.path.exists()

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
            output = self.docker_client.compose.up(services=[], detach=True, pull="never",
                                                     force_recreate=True, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print("Started bench services.")

            richprint.change_head("Creating bench and bench site.")
            self.benchops.create_fm_bench()
            self.sync_bench_config_configuration()

            self.switch_bench_env()

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
        include_custom_workers = False,
        reconfigure_supervisor: bool = False,
        reconfigure_common_site_config: bool = False,
        sync_dev_packages: bool = False,
    ):
        """
        Starts the bench.
        """

        self.benchops.check_required_docker_images_available()

        # Reconfigure common_site_config.json if required
        if reconfigure_common_site_config:
            richprint.print("Reconfiguring common_site_config with defaults")
            global_db_info = self.services.database_manager.database_server_info
            self.sync_bench_common_site_config(global_db_info.host, global_db_info.port)

        richprint.change_head("Starting bench services")

        self.docker_ops.start(services=[], force_recreate=force, pull="never")

        # start admin-tools if exists
        if self.admin_tools.compose_file_manager.compose_path.exists():
            richprint.change_head("Starting admin tools services")
            self.admin_tools.enable(force_recreate_container=force)
            richprint.print("Started admin tools services.")

            # Check if nginx service is stopped and restart if needed
            if not self._is_service_running('nginx'):
                self.docker_ops.start(services=['nginx'], force_recreate=False, pull="never")

        self.benchops.is_required_services_available()

        # Reconfigure supervisord if requested
        if reconfigure_supervisor:
            richprint.print("Reconfiguring supervisord")
            self.benchops.setup_supervisor(force=True)

        # Reconfigure workers if requested
        if reconfigure_workers:
            richprint.print("Reconfiguring workers")
            self.sync_workers_compose(include_default_workers=include_default_workers, include_custom_workers=include_custom_workers)

        # Sync dev packages if requested
        if sync_dev_packages:
            richprint.print("Syncing dev packages")
            if self.bench_config.environment_type == FMBenchEnvType.dev:
                self.install_dev_packages()
            else:
                self.remove_dev_packages()

        self.switch_bench_env()

        # Sync bench config changes if requested
        if sync_bench_config_changes:
            richprint.print("Syncing bench configuration changes")
            self.sync_bench_config_configuration()

        # start workers if exists
        if self.workers.compose_file_manager.exists():
            richprint.change_head("Starting bench workers services")
            output = self.workers.docker_client.compose.up(services=[], detach=True, pull="never",
                                                            force_recreate=force, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print("Started bench workers services.")

        self.save_bench_config()
        richprint.print("Started bench services.")

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
        richprint.change_head("Stopping bench services")
        self.docker_ops.stop(timeout=10)
        richprint.print("Stopped bench services.")

        if self.workers.compose_file_manager.exists():
            richprint.change_head("Starting bench workers services")
            output = self.workers.docker_client.compose.stop(services=[], timeout=10, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print("Started bench workers services")

        # stop admin_tools if exists
        if self.admin_tools.compose_file_manager.exists():
            richprint.change_head("Stopped bench admin tools services")
            self.admin_tools.disable()
            richprint.print("Stopped bench admin tools services.")

    def remove_containers_and_dirs(self):
        """
        Removes the site by stopping and removing the containers associated with it,
        and deleting the site directory.

        Returns:
            bool: True if the site is successfully removed, False otherwise.
        """
        # TODO handle low level errors like read only, write only, etc.
        if self.compose_file_manager.exists():
            richprint.change_head("Removing bench containers.")
            self.docker_ops.remove_containers(remove_volumes=True, timeout=5)
            richprint.print("Removed bench containers.")
        else:
            richprint.warning('Bench compose file not found. Skipping containers removal.')

        if self.workers.compose_file_manager.exists():
            richprint.change_head("Removing bench workers containers.")
            output = self.workers.docker_client.compose.down(remove_orphans=True, volumes=True, timeout=5, stream=True)
            richprint.live_lines(output, padding=(0, 0, 0, 2))
            richprint.print("Removed bench workers containers.")
        else:
            richprint.warning('Bench workers compose file not found. Skipping containers removal.')

        if self.admin_tools.compose_file_manager.exists():
            richprint.change_head("Removing bench admin tools containers.")
            # down_service equivalent: stop + remove containers + volumes
            try:
                self.admin_tools.docker_client.compose.down(
                    remove_orphans=True,
                    volumes=True,
                    timeout=5,
                    stream=True
                )
            except Exception:
                pass  # Best effort cleanup
            richprint.print("Removed bench admin tools containers.")
        else:
            richprint.warning('Bench admin tools compose file not found. Skipping containers removal.')

        richprint.change_head("Removing all bench files and directories.")
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

    def sync_workers_compose(self, force_recreate: bool = False, setup_supervisor: bool = True, include_default_workers: bool = True, include_custom_workers: bool = True):
        self.worker_coordinator.sync_workers_compose(
            force_recreate=force_recreate,
            setup_supervisor=setup_supervisor,
            include_default_workers=include_default_workers,
            include_custom_workers=include_custom_workers
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
        self.ssl.create_certificate(self.bench_config.alias_domains)
        self.save_bench_config()

    def has_certificate(self):
        return self.ssl.has_certificate()

    def remove_certificate(self):
        self.ssl.remove_certificate(self.bench_config.alias_domains)
        self.bench_config.ssl = SSLCertificate(domain=self.name, ssl_type=SUPPORTED_SSL_TYPES.none)
        self.save_bench_config()

    def update_certificate(self, certificate: SSLCertificate, raise_error: bool = True):
        result = self.ssl.update_certificate(certificate, self.bench_config.alias_domains, raise_error)
        if result:
            self.bench_config.ssl = certificate
        return result

    def renew_certificate(self):
        return self.ssl.renew_certificate(self.bench_config.alias_domains)

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
        # Backup current alias domains for rollback
        backup_aliases = copy.deepcopy(self.bench_config.alias_domains or [])
        current_aliases = set(backup_aliases)
        
        # Validate and prepare updates
        add_list = add_domains if add_domains else []
        remove_list = remove_domains if remove_domains else []
        
        # Validation: Check for primary domain in operations
        if self.name in add_list:
            richprint.warning(f"Skipping '{self.name}' - primary domain cannot be added as alias.")
            add_list = [d for d in add_list if d != self.name]
        
        if self.name in remove_list:
            richprint.stop()
            raise ValueError(
                f"Cannot remove primary domain '{self.name}'. Only alias domains can be removed."
            )
        
        # Add domains
        added_domains = []
        for domain in add_list:
            if domain in current_aliases:
                richprint.warning(f"Domain '{domain}' is already an alias. Skipping.")
            else:
                current_aliases.add(domain)
                added_domains.append(domain)
        
        # Check for wildcard domains and warn about DNS-01 requirement
        for domain in added_domains:
            if domain.startswith('*.'):
                richprint.warning(
                    f"Wildcard domain '{domain}' requires DNS-01 challenge and Cloudflare credentials."
                )
        
        # Remove domains
        removed_domains = []
        for domain in remove_list:
            if domain not in current_aliases:
                richprint.warning(f"Domain '{domain}' is not an alias. Skipping.")
            else:
                current_aliases.remove(domain)
                removed_domains.append(domain)
        
        # Check if any changes were made
        if not added_domains and not removed_domains:
            richprint.print("No changes to apply.")
            return
        
        # Display changes
        if added_domains:
            richprint.print(f"Adding aliases: {', '.join(added_domains)}")
        if removed_domains:
            richprint.print(f"Removing aliases: {', '.join(removed_domains)}")
        
        # Update alias list - only at bench level now
        updated_aliases = sorted(list(current_aliases))
        self.bench_config.alias_domains = updated_aliases
        
        try:
            # Only regenerate certificate if SSL is active
            if self.has_certificate():
                richprint.change_head("Regenerating SSL certificate with updated domains")
                self.certificate_manager.generate_certificate(self.bench_config.alias_domains)
                richprint.print("Certificate regenerated successfully.")
            
            # Always save config and restart services
            richprint.change_head("Saving configuration")
            self.save_bench_config()
            richprint.print("Configuration saved.")
            
            richprint.change_head("Updating services")
            output = self.docker_client.compose.stop(services=[], timeout=10, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            
            # Delete nginx config to force regeneration with new domains
            nginx_config_path = self.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
            if nginx_config_path.exists():
                nginx_config_path.unlink()
            
            self.generate_compose(self.bench_config.export_to_compose_inputs())
            output = self.docker_client.compose.up(services=[], detach=True, pull="never",
                                                     force_recreate=True, stream=self.quiet)
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))
            
            # Start admin tools if they exist
            if self.admin_tools.compose_file_manager.compose_path.exists():
                self.admin_tools.enable(force_recreate_container=True)
            
            # Ensure required services are available
            self.benchops.is_required_services_available()
            
            # Start Frappe supervisor processes (critical for app to be accessible)
            self.switch_bench_env()
            
            # Start workers if they exist
            if self.workers.compose_file_manager.exists():
                output = self.workers.docker_client.compose.up(services=[], detach=True, pull="never",
                                                                force_recreate=True, stream=self.quiet)
                if self.quiet:
                    richprint.live_lines(output, padding=(0, 0, 0, 2))
            
            richprint.print("Services restarted with updated configuration.")
            
        except Exception as e:
            # Rollback on failure
            self.bench_config.alias_domains = backup_aliases
            richprint.stop()
            self.logger.error(f"Failed to update alias domains: {e}")
            raise Exception(f"Failed to update alias domains: {e}")

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
                if not self._is_service_running(service):
                    richprint.exit(
                        f"Cannot show logs. [blue]{self.name}[/blue]'s compose service '{service}' not running!"
                    )
                self.docker_ops.logs(services=[service.value], follow=follow)

        except KeyboardInterrupt:
            richprint.stdout.print("Detected CTRL+C. Exiting..")

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
                    admin_tools_running = all(
                        running_statuses.get(service) == "running"
                        for service in services
                    )
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
                    richprint.print("Using admin_password defined in site_config.json")

            if not admin_pass:
                common_site_config = self.get_common_bench_config()
                if 'admin_password' in common_site_config:
                    admin_pass = common_site_config['admin_password']
                    richprint.print("Using admin_password defined in common_site_config.json")

        if not admin_pass:
            admin_pass = richprint.prompt_ask(prompt=f"Please enter admin password for site {self.name}")

        richprint.change_head(f"Resetting bench site {self.name}")

        self.benchops.reset_bench_site(admin_pass)
        self.set_bench_site_config({'admin_password': admin_pass})

        richprint.print(f"Reset bench site {self.name}")

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
            richprint.change_head(f"Restarting web services - {service}")
            is_restarted = self.restart_supervisor_service(service)
            if is_restarted:
                richprint.print(f"Restarted web services - {service}")

    def restart_redis_services_containers(self):
        """Restarts redis containers"""

        redis_services = [
            SiteServicesEnum.redis_cache.value,
            SiteServicesEnum.redis_queue.value,
            SiteServicesEnum.redis_socketio.value,
        ]
        richprint.change_head(f"Restarting redis services - {' '.join(redis_services)}")
        self.docker_ops.restart_services(redis_services)
        richprint.print(f"Restarted redis services - {' '.join(redis_services)}")

    def restart_workers_containers_services(self):
        """Restarts workers and schedule containers"""
        self.worker_coordinator.restart_workers_containers_services()
