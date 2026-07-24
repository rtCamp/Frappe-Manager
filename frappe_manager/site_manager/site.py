import itertools
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    SiteServicesEnum,
)
from frappe_manager.docker import ComposeFile, DockerClient, DockerException
from frappe_manager.logger import ContextualLogger
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.site_manager.exceptions import (
    BenchException,
    BenchRemoveDirectoryError,
)
from frappe_manager.site_manager.modules.bench_admin_tools import BenchAdminTools
from frappe_manager.site_manager.modules.bench_app import BenchAppManager
from frappe_manager.site_manager.modules.bench_database import BenchDatabase
from frappe_manager.site_manager.modules.bench_devtools import BenchDevTools
from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps
from frappe_manager.site_manager.modules.bench_info import BenchInfo
from frappe_manager.site_manager.modules.bench_orchestrator import BenchOrchestrator
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager
from frappe_manager.site_manager.modules.bench_ssl import BenchSSL
from frappe_manager.site_manager.modules.bench_supervisor import BenchSupervisor
from frappe_manager.site_manager.modules.bench_workers import BenchWorkerCoordinator, BenchWorkers
from frappe_manager.site_manager.modules.upload_limit_manager import UploadLimitManager
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.nginx_controller import NginxController
from frappe_manager.ssl_manager.proxy_storage import ProxyStoragePaths
from frappe_manager.ssl_manager.service_factory import create_certificate_service
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig
from frappe_manager.utils.helpers import (
    log_file,
    save_dict_to_file,
)
from frappe_manager.utils.site import domain_level


class Bench:
    def __init__(
        self,
        logger: ContextualLogger,
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
        self.output = output_handler or RichOutputHandler()
        self.services = services
        self.backup_path = self.path / "backups"
        self.bench_config: BenchConfig = bench_config
        self.logger = logger.child(bench=name)

        self.compose_file_manager = compose_file_manager
        self.docker_client = docker_client

        # Initialize specialized modules
        self.docker_ops = BenchDockerOps(
            logger=self.logger,
            docker_client=docker_client,
            compose_file_manager=compose_file_manager,
            config=bench_config,
            path=path,
            output_handler=self.output,
        )
        self.supervisor = BenchSupervisor(
            logger=self.logger,
            docker_client=docker_client,
            config=bench_config,
            bench_name=name,
            output_handler=self.output,
        )

        # Initialize local nginx proxy components
        self.bench_proxy_storage = ProxyStoragePaths("nginx", self.compose_file_manager)
        self.bench_nginx_controller = NginxController("nginx", self.compose_file_manager, self.docker_client)

        # For backward compatibility with admin_tools
        # Create a simple proxy manager object with required attributes
        self.proxy_manager = type(
            "ProxyManager",
            (),
            {
                "dirs": self.bench_proxy_storage.dirs,
                "restart": self.bench_nginx_controller.restart,
                "reload": self.bench_nginx_controller.reload,
            },
        )()

        self.admin_tools = BenchAdminTools(self, self.proxy_manager, verbose=verbose, output_handler=self.output)

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

        link_manager = CertificateLinkManager(ssl_storage_config)

        def certificate_service_factory(cert, storage_cfg, output_handler):
            return create_certificate_service(self.logger, cert, storage_cfg, output_handler)

        self.certificate_manager = SSLCertificateManager(
            logger=self.logger,
            certificates=self.bench_config.ssl_certificates,
            service_factory=certificate_service_factory,
            link_manager=link_manager,
            nginx_controller=services.nginx_controller,
            storage_config=ssl_storage_config,
            config_save_callback=self.save_bench_config,
            output_handler=self.output,
        )

        self.ssl = BenchSSL(
            certificate_manager=self.certificate_manager,
            bench_name=name,
            is_service_running_fn=self._is_service_running,
        )

        self.devtools = BenchDevTools(
            docker_client=docker_client,
            compose_file_manager=compose_file_manager,
            bench_path=path,
            bench_name=name,
            is_running_fn=lambda: self.running,
            output_handler=self.output,
        )
        self.devtools.logger = self.logger

        self.database = BenchDatabase(
            bench_name=name,
            bench_path=path,
            services=services,
            set_common_bench_config_fn=self.set_common_bench_config,
            output_handler=self.output,
        )

        self.site_manager = BenchSiteManager(
            logger=self.logger,
            bench_name=name,
            bench_path=path,
            docker_client=docker_client,
            bench_config=bench_config,
            services=services,
            compose_file_manager=compose_file_manager,
            output_handler=self.output,
        )

        self.app_manager = BenchAppManager(
            logger=self.logger,
            bench_name=name,
            bench_path=path,
            docker_client=docker_client,
            bench_config=bench_config,
            output_handler=self.output,
        )

        self.workers = BenchWorkers(self, not verbose, output_handler=self.output)

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
            docker_client=docker_client,
            output_handler=self.output,
        )

        self.worker_coordinator = BenchWorkerCoordinator(
            bench_name=name,
            workers=self.workers,
            supervisor=self.supervisor,
            bench_path=self.path,
            restart_supervisor_service_fn=self.restart_supervisor_service,
            is_running_fn=lambda: self.running,
            docker_ops=self.docker_ops,
            output_handler=self.output,
        )

        # For complex workflows
        self.orchestrator = BenchOrchestrator(logger=self.logger, bench=self, output_handler=self.output)

        if workers_check:
            self.ensure_workers_running_if_available()

        if admin_tools_check:
            self.ensure_admin_tools_running_if_available()

    @classmethod
    def get_object(
        cls,
        bench_name: str,
        services: ServicesManager,
        logger: ContextualLogger | None = None,
        benches_path: Path = CLI_BENCHES_DIRECTORY,
        bench_config_file_name: str = CLI_BENCH_CONFIG_FILE_NAME,
        workers_check: bool = False,
        admin_tools_check: bool = False,
        verbose: bool = False,
        output_handler: OutputHandler | None = None,
    ) -> "Bench":
        if domain_level(bench_name) == 0:
            bench_name = bench_name + ".localhost"

        bench_path = benches_path / bench_name
        bench_config_path: Path = bench_path / bench_config_file_name

        if not bench_path.exists():
            from frappe_manager.site_manager.exceptions import BenchNotFoundError

            raise BenchNotFoundError(bench_name, bench_path)

        compose_file_manager = ComposeFile(bench_path / "docker-compose.yml")
        docker_client = DockerClient(compose_file_path=bench_path / "docker-compose.yml", output=output_handler)

        bench_config: BenchConfig = BenchConfig.import_from_toml(bench_config_path)

        if logger is None:
            from frappe_manager.logger import log as base_log
            from frappe_manager.logger.context import LoggerContext
            from frappe_manager.logger.contextual import ContextualLogger

            logger = ContextualLogger(base_log.get_logger(), context=LoggerContext())

        parms: dict[str, Any] = {
            "logger": logger,
            "name": bench_name,
            "path": bench_path,
            "bench_config": bench_config,
            "compose_file_manager": compose_file_manager,
            "docker_client": docker_client,
            "services": services,
            "workers_check": workers_check,
            "admin_tools_check": admin_tools_check,
        }

        if output_handler is not None:
            parms["output_handler"] = output_handler

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
        extra = {"operation": "config_sync_bench_config", "bench_name": self.name}
        self.logger.debug(f"Syncing bench config configuration: {self.name}", extra_fields=extra)
        try:
            # set developer_mode based on config
            self.set_common_bench_config({"developer_mode": self.bench_config.developer_mode})

            # ssl
            certificate_updated = self.update_certificate(
                self.bench_config.get_primary_certificate(),
                raise_error=False,
            )
            if certificate_updated:
                self.output.print("Certificate Updated")

            # admin tools
            if self.bench_config.admin_tools:
                if not self.admin_tools.compose_file_manager.compose_path.exists():
                    self.sync_admin_tools_compose()
                else:
                    self.admin_tools.enable(force_configure=True)
                self.output.print("Enabled Admin-tools")

            elif not self.admin_tools.compose_file_manager.compose_path.exists():
                self.output.print("Admin tools is already disabled")
            else:
                self.admin_tools.disable()
                self.output.print("Disabled Admin-tools")

            self.output.change_head("Restarting frappe server")
            self.restart_supervisor_service("frappe")
            self.output.print("Restarted frappe server")
            self.logger.info(f"Bench config synchronized: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to sync bench config: {self.name}", extra_fields=extra)
            raise

    def save_bench_config(self, print_message: bool = True):
        extra = {"operation": "config_save_bench_config", "bench_name": self.name, "print_message": print_message}
        self.logger.debug(f"Saving bench config: {self.name}", extra_fields=extra)
        try:
            if print_message:
                self.output.change_head("Saving bench config changes")
            self.bench_config.export_to_toml(self.bench_config.root_path)
            if print_message:
                self.output.print("Saved bench config")
            self.logger.info(f"Bench config saved: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to save bench config: {self.name}", extra_fields=extra)
            raise

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
        extra = {"operation": "bench_create", "bench_name": self.name, "is_template_bench": is_template_bench}
        self.logger.debug(f"Starting bench creation: {self.name}", extra_fields=extra)
        try:
            self.orchestrator.create_bench(is_template_bench)
            self.logger.info(f"Bench created successfully: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to create bench: {self.name}", extra_fields=extra)
            raise

    def set_common_bench_config(self, config: dict):
        """
        Sets the values in the common_site_config.json file.

        Args:
            config (dict): A dictionary containing the key-value pairs
        """
        extra = {"operation": "config_set_common", "bench_name": self.name, "config_keys": list(config.keys())}
        self.logger.debug(f"Setting common bench configuration: {self.name}", extra_fields=extra)
        try:
            common_bench_config_path = self.path / "workspace/frappe-bench/sites/common_site_config.json"
            if not common_bench_config_path.exists():
                raise BenchException(self.name, message=f"File not found {common_bench_config_path.name}.")

            save_dict_to_file(config, common_bench_config_path)
            self.logger.info(f"Common bench configuration set: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to set common bench configuration: {self.name}", extra_fields=extra)
            raise

    def set_bench_site_config(self, config: dict):
        """
        Sets the values in the bench's site site_config.json file.

        Args:
            config (dict): A dictionary containing the key-value pairs
        """
        site_config_path = self.path / "workspace/frappe-bench/sites" / self.name / "site_config.json"
        if not site_config_path.exists():
            raise BenchException(self.name, message=f"File not found {site_config_path.name}.")
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

    def create_compose_dirs(self, copy_runtimes: bool = True) -> bool:
        return self.docker_ops.create_compose_dirs(copy_runtimes=copy_runtimes)

    def start(
        self,
        force: bool = False,
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
        extra = {
            "operation": "bench_start",
            "bench_name": self.name,
            "force": force,
            "reconfigure_workers": reconfigure_workers,
        }
        self.logger.debug(f"Starting bench: {self.name}", extra_fields=extra)
        try:
            self.orchestrator.start_bench(
                force=force,
                reconfigure_workers=reconfigure_workers,
                include_default_workers=include_default_workers,
                include_custom_workers=include_custom_workers,
                reconfigure_supervisor=reconfigure_supervisor,
                reconfigure_common_site_config=reconfigure_common_site_config,
                sync_dev_packages=sync_dev_packages,
            )
            self.logger.info(f"Bench started successfully: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to start bench: {self.name}", extra_fields=extra)
            raise

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
        extra = {"operation": "bench_stop", "bench_name": self.name}
        self.logger.debug(f"Stopping bench: {self.name}", extra_fields=extra)
        try:
            self.docker_ops.stop(timeout=10)

            if self.workers.compose_file_manager.exists():
                self.output.change_head("Stopping bench workers services")
                self.workers.docker_client.compose.stop(services=[], timeout=10)
                self.output.print("Stopped bench workers services")

            # stop admin_tools if exists
            if self.admin_tools.compose_file_manager.exists():
                self.output.change_head("Stopping bench admin tools services")
                self.admin_tools.stop()
                self.output.print("Stopped bench admin tools services")

            self.logger.info(f"Bench stopped successfully: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to stop bench: {self.name}", extra_fields=extra)
            raise

    def remove_containers_and_dirs(self):
        """
        Removes the site by stopping and removing the containers associated with it,
        and deleting the site directory.

        Returns:
            bool: True if the site is successfully removed, False otherwise.
        """
        # TODO handle low level errors like read only, write only, etc.
        if self.compose_file_manager.exists():
            self.output.change_head("Removing bench containers")
            self.docker_ops.remove_containers(remove_volumes=True, timeout=5)
            self.output.print("Removed bench containers")
        else:
            self.output.warning("Bench compose file not found. Skipping containers removal.")

        if self.workers.compose_file_manager.exists():
            self.output.change_head("Removing bench workers containers")
            output = self.workers.docker_client.compose.down(remove_orphans=True, volumes=True, timeout=5, stream=True)
            self.output.live_lines(cast("Iterator[tuple[str, bytes]]", output), padding=(0, 0, 0, 2))
            self.output.print("Removed bench workers containers")
        else:
            self.output.warning("Bench workers compose file not found. Skipping containers removal.")

        if self.admin_tools.compose_file_manager.exists():
            self.output.change_head("Removing bench admin tools containers")
            # down_service equivalent: stop + remove containers + volumes
            try:
                self.admin_tools.docker_client.compose.down(remove_orphans=True, volumes=True, timeout=5, stream=True)
            except Exception:
                pass  # Best effort cleanup
            self.output.print("Removed bench admin tools containers")
        else:
            self.output.warning("Bench admin tools compose file not found. Skipping containers removal.")

        self.output.change_head("Removing all bench files and directories")
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

        self.output.print("Removed all bench files and directories")

    def is_bench_created(self, retry=60, interval=1) -> bool:
        curl_command = "curl -I --max-time {retry} --connect-timeout {retry} {headers} {url}"
        url = "http://localhost"
        headers = ""
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
                    if "HTTP/1.1 200 OK" in line:
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
        start: bool = True,
    ):
        extra = {
            "operation": "workers_sync_compose",
            "bench_name": self.name,
            "force_recreate": force_recreate,
            "setup_supervisor": setup_supervisor,
        }
        self.logger.debug(f"Syncing workers compose for bench: {self.name}", extra_fields=extra)
        try:
            self.worker_coordinator.sync_workers_compose(
                force_recreate=force_recreate,
                setup_supervisor=setup_supervisor,
                include_default_workers=include_default_workers,
                include_custom_workers=include_custom_workers,
                start=start,
            )
            self.logger.info(f"Workers compose synced for bench: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to sync workers compose for bench: {self.name}", extra_fields=extra)
            raise

    def backup_restore_workers_supervisor(self, backup_manager: BackupManager):
        self.worker_coordinator.backup_restore_workers_supervisor(backup_manager)

    def backup_workers_supervisor_conf(self):
        return self.worker_coordinator.backup_workers_supervisor_conf()

    def regenerate_workers_supervisor_conf(self):
        self.worker_coordinator.regenerate_workers_supervisor_conf()

    def get_bench_apps(self):
        return self.info_display.get_bench_apps()

    # this can be plugable
    def get_db_connection_info(self):
        return self.database.get_connection_info()

    def create_certificate(self):
        extra = {"operation": "ssl_create_certificate", "bench_name": self.name}
        self.logger.debug(f"Creating SSL certificate: {self.name}", extra_fields=extra)
        try:
            self.ssl.create_individual_certificates()
            self.save_bench_config()
            self.logger.info(f"SSL certificate created successfully: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to create SSL certificate: {self.name}", extra_fields=extra)
            raise

    def has_certificate(self):
        return self.ssl.has_certificate()

    def remove_certificate(self):
        """
        Remove ALL SSL certificates for this bench.

        This removes certificates for the primary domain and all alias domains,
        including their symlinks, vhost configs, and acme.sh configurations.
        Then clears the certificate list in bench_config.
        """
        extra = {"operation": "ssl_remove_certificate", "bench_name": self.name}
        self.logger.debug(f"Removing SSL certificate: {self.name}", extra_fields=extra)
        try:
            self.ssl.remove_all_certificates()
            # Clear all certificates from config
            self.bench_config.ssl_certificates = []
            self.save_bench_config()
            self.logger.info(f"SSL certificate removed successfully: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to remove SSL certificate: {self.name}", extra_fields=extra)
            raise

    def update_certificate(self, certificate: SSLCertificate, raise_error: bool = True):
        extra = {"operation": "ssl_update_certificate", "bench_name": self.name, "raise_error": raise_error}
        self.logger.debug(f"Updating SSL certificate: {self.name}", extra_fields=extra)
        try:
            result = self.ssl.update_certificate(certificate, raise_error)
            if result:
                self.bench_config.set_primary_certificate(certificate)
            self.logger.info(f"SSL certificate updated: {self.name} (result: {result})", extra=extra)
            return result
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to update SSL certificate: {self.name}", extra_fields=extra)
            raise

    def renew_certificate(self):
        extra = {"operation": "ssl_renew_certificate", "bench_name": self.name}
        self.logger.debug(f"Renewing SSL certificate: {self.name}", extra_fields=extra)
        try:
            result = self.ssl.renew_certificate()
            self.logger.info(f"SSL certificate renewed: {self.name} (result: {result})", extra=extra)
            return result
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to renew SSL certificate: {self.name}", extra_fields=extra)
            raise

    def update_alias_domains(self, add_domains: list[str] | None = None, remove_domains: list[str] | None = None):
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

    def shell(self, compose_service: str, user: str | None, shell_path: str | None = None, use_run: bool = False):
        """
        Spawns a shell for the specified service and user.

        Args:
            service (str): The name of the service.
            user (str | None): The name of the user. If None, defaults to "frappe".
            shell_path (str | None): Path to shell executable (e.g., /bin/sh, /bin/bash).
            use_run (bool): Use 'docker compose run --rm' instead of 'docker compose exec'.

        """
        return self.docker_ops.shell(compose_service, user, shell_path=shell_path, use_run=use_run)

    def execute_command(
        self,
        compose_service: str,
        command: str,
        user: str | None = None,
        shell_path: str | None = None,
        use_run: bool = False,
    ) -> int:
        """
        Execute a single command in the specified service and return exit code.

        Args:
            compose_service: The name of the service
            command: The command to execute
            user: The name of the user (defaults to "frappe" for frappe service)
            shell_path: Path to shell executable (e.g., /bin/sh, /bin/bash)
            use_run: Use 'docker compose run --rm' instead of 'docker compose exec'

        Returns:
            Exit code of the executed command
        """
        return self.docker_ops.execute_command(compose_service, command, user, shell_path=shell_path, use_run=use_run)

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
                log_generators.append(log_file(open(path), follow=follow))

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

    def logs(self, follow: bool, service: str | None = None):
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
                        f"Cannot show logs. [blue]{self.name}[/blue]'s compose service '{service}' not running!",
                    )
                    return
                self.docker_ops.logs(services=[service], follow=follow)

        except KeyboardInterrupt:
            print("Detected CTRL+C. Exiting..")

    def attach_to_bench(self, user: str, extensions: list[str], workdir: str, debugger: bool = False) -> None:
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
        extra = {"operation": "db_remove", "bench_name": self.name}
        self.logger.debug(f"Removing database and user for bench: {self.name}", extra_fields=extra)
        try:
            self.database.remove_database_and_user()
            self.logger.info(f"Database and user removed for bench: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to remove database and user for bench: {self.name}", extra_fields=extra)
            raise

    def remove_bench(self, default_choice: bool = True, delete_db_from_global_db: bool | None = None):
        """
        Removes the bench.

        Args:
            default_choice: If True, defaults to 'no' for confirmation prompt
            delete_db_from_global_db: Whether to delete DB from global-db.
                                     If None, prompts interactively when DB is in global-db.
        """
        extra = {"operation": "bench_remove", "bench_name": self.name, "default_choice": default_choice}
        self.logger.debug(f"Attempting to remove bench: {self.name}", extra_fields=extra)

        params: dict[str, Any] = {}
        params["prompt"] = f"🤔 Do you want to remove [bold][green]'{self.name}'[/bold][/green]"
        params["choices"] = ["yes", "no"]

        if default_choice:
            params["default"] = "no"

        params["required_flag"] = "--yes or -y"
        continue_remove = self.output.prompt_ask(**params)

        if continue_remove == "no":
            self.logger.debug(f"Bench removal cancelled by user: {self.name}", extra_fields=extra)
            return False

        from frappe_manager.output_manager import spinner

        try:
            with spinner(self.output, "Removing bench"):
                try:
                    self.remove_certificate()
                except Exception as e:
                    self.output.warning(str(e))

                try:
                    self._handle_database_deletion(delete_db_from_global_db)
                except Exception as e:
                    self.output.warning(f"Database deletion failed: {e!s}")
                    self.output.warning("Continuing with bench removal...")

                self.remove_containers_and_dirs()

            self.logger.info(f"Bench removed successfully: {self.name}", extra_fields=extra)
            return True
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to remove bench: {self.name}", extra_fields=extra)
            raise

    def _is_using_global_db(self) -> bool:
        """
        Check if bench is using FM's managed global-db service.

        Returns:
            True if bench uses global-db, False otherwise
        """
        try:
            db_info = self.database.get_connection_info()
            db_host = db_info.get("host", "")

            return db_host == "global-db"
        except Exception:
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
        extra = {"operation": "db_handle_deletion", "bench_name": self.name}
        self.logger.debug(f"Handling database deletion for bench: {self.name}", extra_fields=extra)
        try:
            is_global_db = self._is_using_global_db()

            if not is_global_db:
                self.output.print("Bench is not using FM's managed global-db. Skipping database deletion")
                self.logger.info(f"Skipping database deletion - not using global-db: {self.name}", extra_fields=extra)
                return

            should_delete = delete_db_from_global_db

            if should_delete is None:
                params = {
                    "prompt": f"🗄️  Do you want to remove the database '[bold]{self.name}[/bold]' from global-db?",
                    "choices": ["yes", "no"],
                    "default": "yes",
                    "required_flag": "--delete-db-from-global-db or --no-delete-db-from-global-db",
                }
                choice = self.output.prompt_ask(**params)
                should_delete = choice == "yes"

            if should_delete:
                self.remove_database_and_user()
            else:
                self.output.print("Skipping database deletion from global-db")
                self.logger.info(f"Database deletion skipped by user: {self.name}", extra_fields=extra)

            self.logger.info(f"Database deletion handled: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to handle database deletion for bench: {self.name}", extra_fields=extra)
            raise

    def ensure_workers_running_if_available(self):
        extra = {"operation": "workers_ensure_running", "bench_name": self.name}
        self.logger.debug(f"Ensuring workers running if available for bench: {self.name}", extra_fields=extra)
        try:
            self.worker_coordinator.ensure_workers_running_if_available()
            self.logger.info(f"Workers status ensured for bench: {self.name}", extra_fields=extra)
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to ensure workers for bench: {self.name}", extra_fields=extra)
            raise

    def ensure_admin_tools_running_if_available(self):
        if self.admin_tools.compose_file_manager.exists():
            if self.bench_config.admin_tools:
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
                        if service == "running":
                            atleast_one_service_running = True
                except Exception:
                    atleast_one_service_running = False

                if atleast_one_service_running:
                    self.admin_tools.disable()

    def sync_admin_tools_compose(self):
        extra = {"operation": "admin_tools_sync_compose", "bench_name": self.name}
        self.logger.debug(f"Syncing admin tools compose for bench: {self.name}", extra_fields=extra)
        try:
            self.admin_tools.generate_compose(self.services.database_manager.database_server_info.host)
            restart_required = self.admin_tools.enable(force_recreate_container=True)
            self.logger.info(f"Admin tools compose synced for bench: {self.name}", extra_fields=extra)
            return restart_required
        except Exception as e:
            extra["error"] = str(e)
            self.logger.exception(f"Failed to sync admin tools compose for bench: {self.name}", extra_fields=extra)
            raise

    def frappe_service_run_command(self, command: str):
        try:
            self.docker_client.compose.exec("frappe", command, user="frappe", stream=False)
        except DockerException as e:
            raise BenchException("frappe", f"Faild to run {command} in frappe service.")

    def get_apps_dev_requirements(self) -> list[str]:
        """Parse pip requirement string to package name and version"""
        return self.devtools.get_apps_dev_requirements()

    def remove_dev_packages(self):
        return self.devtools.remove_dev_packages()

    def install_dev_packages(self):
        return self.devtools.install_dev_packages()

    def is_supervisord_running(self, interval: int = 2, timeout: int = 30):
        return self.supervisor.is_supervisord_running(interval, timeout)

    def reset(self, admin_password: str | None = None):
        admin_pass = None

        if admin_password:
            admin_pass = admin_password
        else:
            if not admin_pass:
                site_config = self.get_bench_site_config()
                if "admin_password" in site_config:
                    admin_pass = site_config["admin_password"]
                    self.output.print("Using admin_password defined in site_config.json")

            if not admin_pass:
                common_site_config = self.get_common_bench_config()
                if "admin_password" in common_site_config:
                    admin_pass = common_site_config["admin_password"]
                    self.output.print("Using admin_password defined in common_site_config.json")

        if not admin_pass:
            admin_pass = self.output.prompt_ask(
                prompt=f"Please enter admin password for site {self.name}",
                required_flag="--admin-pass",
            )

        self.output.change_head(f"Resetting bench site {self.name}")

        self.site_manager.reset_bench_site(admin_pass)
        self.set_bench_site_config({"admin_password": admin_pass})

        self.output.print(f"Reset bench site {self.name}")

    def restart_supervisor_service(
        self,
        service: str,
        docker_client_obj: DockerClient | None = None,
        timeout: int = 30,
        interval: int = 1,
        force: bool = False,
    ):
        return self.supervisor.restart_supervisor_service(service, docker_client_obj, timeout, interval, force)

    def restart_web_containers_services(self, use_container_restart: bool = False, force: bool = False):
        """
        Restarts frappe server and socketio containers.

        Args:
            use_container_restart: If True, restart entire containers. If False, restart supervisor processes.
            force: If True, use aggressive restart (timeout=0 for container, stop+start for supervisor).
        """
        web_services = [
            SiteServicesEnum.frappe.value,
            SiteServicesEnum.socketio.value,
        ]

        if use_container_restart:
            self.docker_ops.restart_services(web_services, force=force)
        else:
            for service in web_services:
                self.output.change_head(f"Restarting web services - {service}")
                is_restarted = self.restart_supervisor_service(service, force=force)
                if is_restarted:
                    action = "Stopped and started" if force else "Restarted"
                    self.output.print(f"{action} supervisor processes - {service}")

    def restart_redis_services_containers(self):
        """Restarts redis containers"""

        redis_services = [
            SiteServicesEnum.redis_cache.value,
            SiteServicesEnum.redis_queue.value,
        ]
        self.output.change_head(f"Restarting redis services - {' '.join(redis_services)}")
        self.docker_ops.restart_services(redis_services)
        self.output.print(f"Restarted redis services - {' '.join(redis_services)}")

    def restart_nginx_service(self, force: bool = False):
        """
        Restarts nginx container.

        Args:
            force: If True, use timeout=0 for immediate kill. If False, use default graceful timeout.
        """
        nginx_service = [SiteServicesEnum.nginx.value]
        self.output.change_head("Restarting nginx service")
        self.docker_ops.restart_services(nginx_service, force=force)
        action = "Force restarted" if force else "Restarted"
        self.output.print(f"{action} nginx service")

    def restart_workers_containers_services(self, use_container_restart: bool = False, force: bool = False):
        """Restarts workers and schedule containers"""
        self.worker_coordinator.restart_workers_containers_services(
            use_container_restart=use_container_restart,
            force=force,
        )

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
        import re

        # Validate format (e.g., "50M", "100M", "500M", "1G")
        if not re.match(r"^\d+[MG]$", upload_limit, re.IGNORECASE):
            raise BenchException(
                self.name,
                message=f"Invalid upload limit format: '{upload_limit}'. Use format like '50M' or '1G'",
            )

        # 1. Update site_config.json (convert to bytes)
        size_bytes = self._parse_size_to_bytes(upload_limit)
        self.set_bench_site_config({"max_file_size": size_bytes})
        self.output.print(f"Updated site_config.json (max_file_size: {size_bytes} bytes)")

        # 2. Update BenchConfig (will affect nginx template on restart)
        self.bench_config.upload_limit = upload_limit.upper()
        self.save_bench_config()
        self.output.print("Updated bench configuration")

        # 2b. Regenerate docker-compose to include new environment variable
        inputs = self.bench_config.export_to_compose_inputs()
        self.generate_compose(inputs)
        self.output.print("Regenerated docker-compose configuration")

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
            f"Upload size limit updated to {upload_limit} (site_config: {size_bytes} bytes, nginx: {upload_limit.lower()})",
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

        match = re.match(r"^(\d+)([MG])$", size_str, re.IGNORECASE)
        if not match:
            raise BenchException(
                self.name,
                message=f"Invalid size format: '{size_str}'. Expected format: <number><unit> (e.g., '50M', '1G')",
            )

        value = int(match.group(1))
        unit = match.group(2).upper()

        if unit == "M":
            return value * 1024 * 1024  # Convert MB to bytes
        if unit == "G":
            return value * 1024 * 1024 * 1024  # Convert GB to bytes

        # Should never reach here due to regex validation
        raise BenchException(self.name, message=f"Unsupported unit: {unit}")

    def get_available_services(self) -> list[str]:
        """
        Get all available services from all compose files.

        Dynamically discovers services from:
        - docker-compose.yml (main services: frappe, nginx, redis, etc.)
        - docker-compose.workers.yml (workers: schedule, default-worker, short-worker, long-worker, custom workers)
        - docker-compose.admin-tools.yml (admin tools: adminer, mailpit)

        Returns:
            List of available service names across all compose files
        """
        services = []

        # Get services from main compose file
        if self.compose_file_manager.compose_path.exists():
            services.extend(self.compose_file_manager.get_services_list())

        # Get services from workers compose file
        workers_compose_path = self.path / "docker-compose.workers.yml"
        if workers_compose_path.exists():
            services.extend(self.workers.compose_file_manager.get_services_list())

        # Get services from admin tools compose file
        admin_tools_compose_path = self.path / "docker-compose.admin-tools.yml"
        if admin_tools_compose_path.exists():
            services.extend(self.admin_tools.compose_file_manager.get_services_list())

        return services
