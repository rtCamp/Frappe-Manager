"""
BenchSiteManager - Frappe Site Lifecycle Management Module

This module handles all Frappe site-related operations within a bench including
site creation, deletion, migration, reset, and status checking.

Extracted from the monolithic Bench class and BenchOperations for better
separation of concerns.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import cast

from frappe_manager import CLI_DEFAULT_DELIMETER
from frappe_manager.docker import DOCKER_LINE_NOISE, DockerClient, DockerException
from frappe_manager.docker.compose_file import ComposeFile
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.exceptions import (
    BenchOperationBenchSiteCreateFailed,
    BenchOperationException,
    BenchOperationWaitForRequiredServiceFailed,
)
from frappe_manager.utils.helpers import get_redis_cache_addr, get_redis_queue_addr


class BenchSiteManager:
    """
    Manages Frappe site lifecycle operations within a bench.

    This module is responsible for all site-related operations including:
    - Site creation and initialization
    - Site deletion and cleanup
    - Site migration and updates
    - Site reset (reinstall)
    - Site status checking
    - Service availability checks

    The module encapsulates bench command execution and provides a clean
    interface for site management operations.

    Attributes:
        bench_name: Name of the bench/site
        bench_path: Path to the bench directory
        docker_client: Docker client for container operations
        bench_config: Bench configuration object
        services: Services manager for database/Redis access
        logger: Logger instance
        frappe_bench_dir: Path to frappe-bench directory inside container
        bench_cli_cmd: Base bench command prefix

    Example:
        >>> site_manager = BenchSiteManager(
        ...     bench_name="example.localhost",
        ...     bench_path=Path("/home/user/frappe/example.localhost"),
        ...     docker_client=docker_client,
        ...     bench_config=bench_config,
        ...     services=services,
        ... )
        >>> site_manager.create_site(admin_pass="admin")
        >>> if site_manager.is_site_created():
        ...     print("Site created successfully")
    """

    def __init__(
        self,
        logger: ContextualLogger,
        bench_name: str,
        bench_path: Path,
        docker_client: DockerClient,
        bench_config: BenchConfig,
        services: ServicesManager,
        compose_file_manager: ComposeFile | None = None,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchSiteManager.

        Args:
            logger: Contextual logger for audit/debug logging
            bench_name: Name of the bench (typically the site domain)
            bench_path: Path to the bench directory on host
            docker_client: Docker client for container operations
            bench_config: Bench configuration object
            services: Services manager providing database/Redis access
            compose_file_manager: Optional ComposeFile instance for the bench's
                docker-compose file. When provided, ``wait_for_required_services``
                will skip health checks for any required services that are
                marked as disabled in that compose file. Pass ``None`` (default)
                to always perform all health checks regardless of service profiles.
            output_handler: Optional output handler for displaying information
        """
        self.logger = logger.child(component="site_manager")
        self.bench_name = bench_name
        self.bench_path = bench_path
        self.docker_client = docker_client
        self.bench_config = bench_config
        self.services = services
        self.compose_file_manager = compose_file_manager
        self.output = output_handler or RichOutputHandler()

        self.frappe_bench_dir: Path = bench_path / "workspace" / "frappe-bench"
        self.bench_cli_cmd = ["/opt/user/.bin/bench"]

    def is_site_created(self, site_name: str | None = None) -> bool:
        """
        Check if a Frappe site exists in the bench.

        Args:
            site_name: Name of the site to check. Defaults to bench_name.

        Returns:
            True if the site exists, False otherwise.

        Example:
            >>> if site_manager.is_site_created():
            ...     print("Site already exists")
        """
        if site_name is None:
            site_name = self.bench_name

        site_path: Path = self.frappe_bench_dir / "sites" / site_name
        return site_path.exists()

    def wait_for_required_services(self, timeout: int = 120) -> None:
        """
        Wait for required services (database, Redis) to be available.

        This method checks if database and Redis services are reachable
        before proceeding with site operations. It will block until all
        services are available or timeout is reached.

        Args:
            timeout: Maximum time to wait in seconds (default: 120)

        Raises:
            BenchOperationWaitForRequiredServiceFailed: If any service is not available

        Example:
            >>> site_manager.wait_for_required_services(timeout=60)
        """
        self.output.change_head("Checking if required services are available")

        db_info = self.services.database_manager.database_server_info
        cache_host, cache_port = get_redis_cache_addr(self.bench_config.container_name_prefix)
        queue_host, queue_port = get_redis_queue_addr(self.bench_config.container_name_prefix)

        candidates = [
            (self.services.compose_file_manager, db_info.host, db_info.host, db_info.port),
            (self.compose_file_manager, "redis-cache", cache_host, cache_port),
            (self.compose_file_manager, "redis-queue", queue_host, queue_port),
        ]

        for cfm, compose_service, host, port in candidates:
            if cfm and cfm.is_service_profile_disabled(compose_service):
                continue
            output: SubprocessOutput = self._wait_for_service(host=host, port=port, timeout=timeout)
            if output.combined:
                command_output = output.combined[-1].replace("wait-for-it: ", "")
                service_name = command_output.split(" ")[0]
                simplified_service_name = service_name.split(":")[0]
                simplified_service_name = simplified_service_name.split(CLI_DEFAULT_DELIMETER)[-1]
                self.output.print(command_output.replace(service_name, simplified_service_name), highlight=False)

    def _wait_for_service(self, host: str, port: int, timeout: int = 120) -> SubprocessOutput:
        """
        Wait for a specific service to be available.

        Args:
            host: Service hostname
            port: Service port
            timeout: Maximum time to wait in seconds

        Returns:
            SubprocessOutput with the wait-for-it command output

        Raises:
            BenchOperationWaitForRequiredServiceFailed: If service is not available
        """
        return cast(
            "SubprocessOutput",
            self._container_run(
                f"wait-for-it -t {timeout} {host}:{port}",
                raise_exception_obj=BenchOperationWaitForRequiredServiceFailed(
                    bench_name=self.bench_name,
                    host=host,
                    port=str(port),
                    timeout=timeout,
                ),
                capture_output=True,
            ),
        )

    def create_bench_site(self, admin_pass: str | None = None, force: bool = False) -> None:
        """
        Create a new Frappe site in the bench.

        This method runs the 'bench new-site' command with appropriate database
        credentials and configuration. It also sets the site as default and
        enables the scheduler.

        Args:
            admin_pass: Administrator password. Defaults to bench_config.admin_pass.

        Raises:
            BenchOperationBenchSiteCreateFailed: If site creation fails
            BenchOperationException: If post-creation setup fails

        Example:
            >>> site_manager.create_bench_site(admin_pass="secure_password")
        """
        if admin_pass is None:
            admin_pass = self.bench_config.admin_pass

        # Build new-site command
        new_site_command = self.bench_cli_cmd + ["new-site"]
        new_site_command += ["--db-root-password", self.services.database_manager.database_server_info.password]
        if self.bench_config.db_name:
            new_site_command += ["--db-name", self.bench_config.db_name]
        new_site_command += ["--db-host", self.services.database_manager.database_server_info.host]
        new_site_command += ["--admin-password", admin_pass]
        new_site_command += ["--db-port", str(self.services.database_manager.database_server_info.port)]
        new_site_command += ["--verbose", "--mariadb-user-host-login-scope", "%"]
        if force:
            # Image runtime pre-binds sites/<site>, so `compose up` created an empty dir;
            # --force lets new-site populate that existing (empty) dir instead of aborting.
            new_site_command += ["--force"]
        new_site_command += [self.bench_name]

        new_site_command = " ".join(new_site_command)

        # Create the site
        self._container_run(new_site_command, raise_exception_obj=BenchOperationBenchSiteCreateFailed(self.bench_name))

        # Set as default site
        self._container_run(
            " ".join(self.bench_cli_cmd + [f"use {self.bench_name}"]),
            raise_exception_obj=BenchOperationException(
                self.bench_name,
                f"Failed to set {self.bench_name} as default site.",
            ),
        )

        # Enable scheduler
        self._container_run(
            " ".join(self.bench_cli_cmd + [f"--site {self.bench_name} scheduler enable"]),
            raise_exception_obj=BenchOperationException(
                self.bench_name,
                f"Failed to enable {self.bench_name}'s scheduler.",
            ),
        )

    def reset_bench_site(self, admin_password: str) -> None:
        """
        Reset (reinstall) a Frappe site, wiping all data.

        This method runs 'bench reinstall' which drops and recreates the
        site's database, effectively resetting it to a fresh state.

        Args:
            admin_password: New administrator password for the reset site

        Raises:
            BenchOperationException: If site reset fails

        Warning:
            This operation is destructive and will delete all site data!

        Example:
            >>> site_manager.reset_bench_site(admin_password="new_admin_pass")
        """
        global_db_info = self.services.database_manager.database_server_info

        reset_bench_site_command = self.bench_cli_cmd + ["--site", self.bench_name]
        reset_bench_site_command += ["reinstall", "--admin-password", admin_password]
        reset_bench_site_command += ["--db-root-username", global_db_info.user]
        reset_bench_site_command += ["--db-root-password", global_db_info.password]
        reset_bench_site_command += ["--yes"]

        reset_bench_site_command = " ".join(reset_bench_site_command)

        self._container_run(
            reset_bench_site_command,
            raise_exception_obj=BenchOperationException(
                bench_name=self.bench_name,
                message=f"Failed to reset bench site {self.bench_name}.",
            ),
        )

    def _container_run(
        self,
        command: str,
        raise_exception_obj: BenchOperationException | None = None,
        capture_output: bool = False,
        user: str = "frappe",
        workdir: str = "/workspace/frappe-bench",
        service: str = "frappe",
        use_run: bool = False,
    ) -> SubprocessOutput | None:
        """
        Execute a command inside the bench container.

        This is an internal helper method that wraps docker_client.compose.exec
        or docker_client.compose.run depending on use_run parameter.

        Args:
            command: Shell command to execute
            raise_exception_obj: Exception to raise on failure
            capture_output: Whether to capture output instead of streaming
            user: User to run command as (default: frappe)
            workdir: Working directory (default: /workspace/frappe-bench)
            service: Docker service name (default: frappe)
            use_run: If True, use 'docker compose run --rm' instead of 'exec' (default: False)

        Returns:
            SubprocessOutput if capture_output=True, None otherwise

        Raises:
            BenchOperationException: If command fails and raise_exception_obj is provided
            DockerException: If command fails and no exception object provided
        """
        try:
            if use_run:
                wrapped_command = f"cd {workdir} && {command}"
                run_command = f"/bin/bash -c '{wrapped_command}'"
                if capture_output:
                    output = cast(
                        "SubprocessOutput",
                        self.docker_client.compose.run(
                            service=service,
                            command=run_command,
                            rm=True,
                            stream=False,
                            entrypoint="/exec-entrypoint.sh",
                        ),
                    )
                    return output
                output = cast(
                    "Iterator[tuple[str, bytes]]",
                    self.docker_client.compose.run(
                        service=service,
                        command=run_command,
                        rm=True,
                        entrypoint="/exec-entrypoint.sh",
                        stream=True,
                    ),
                )
                self.output.live_lines(output, line_filters=DOCKER_LINE_NOISE)
            else:
                exec_command = f"/bin/bash -c '{command}'"
                if capture_output:
                    output = cast(
                        "SubprocessOutput",
                        self.docker_client.compose.exec(
                            service=service,
                            command=exec_command,
                            user=user,
                            workdir=workdir,
                            stream=False,
                        ),
                    )
                    return output
                output = cast(
                    "Iterator[tuple[str, bytes]]",
                    self.docker_client.compose.exec(
                        service=service,
                        command=exec_command,
                        workdir=workdir,
                        user=user,
                        stream=True,
                    ),
                )
                self.output.live_lines(output, line_filters=DOCKER_LINE_NOISE)

        except DockerException as e:
            if raise_exception_obj:
                raise_exception_obj.set_output(e.output)
                raise raise_exception_obj
            raise e
