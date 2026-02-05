"""
BenchSupervisor - Supervisor Process Management Module

This module handles Supervisor process management for bench services.
Extracted from the monolithic Bench class for better separation of concerns.
"""

import time

from frappe_manager.docker import DockerClient, DockerException
from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.exceptions import BenchOperationException


class BenchSupervisor:
    """Manages Supervisor process and worker configuration."""

    def __init__(
        self,
        logger: ContextualLogger,
        docker_client: DockerClient,
        config: BenchConfig,
        bench_name: str,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchSupervisor.

        Args:
            logger: Contextual logger for audit/debug logging
            docker_client: Docker client for operations
            config: Bench configuration
            bench_name: Name of the bench
            output_handler: Optional output handler for displaying information
        """
        self.logger = logger.child(component="supervisor")
        self.docker_client = docker_client
        self.config = config
        self.bench_name = bench_name
        self.output = output_handler or RichOutputHandler()

    def is_supervisord_running(self, interval: int = 2, timeout: int = 30) -> bool:
        """
        Check if supervisord is running.

        Args:
            interval: Check interval in seconds
            timeout: Maximum time to wait in seconds

        Returns:
            True if supervisord is running, False otherwise
        """
        for i in range(timeout):
            try:
                status_command = "supervisorctl -c /opt/user/supervisord.conf status all"
                output = self.docker_client.compose.exec("frappe", status_command, user="frappe", stream=False)
                return True
            except DockerException as e:
                if any("frappe-bench" in s for s in e.output.combined):
                    return True
                time.sleep(interval)
                continue
        return False

    def restart_supervisor_service(
        self,
        service: str,
        docker_client_obj: DockerClient | None = None,
        timeout: int = 30,
        interval: int = 1,
        force: bool = False,
    ) -> bool:
        """
        Restart a supervisor service.

        Args:
            service: Service name to restart
            docker_client_obj: Optional Docker client (uses self.docker_client if None)
            timeout: Timeout in seconds (used for socket availability check after restart)
            interval: Check interval in seconds
            force: If True, stop then start processes (hard restart). If False, use restart command (graceful).

        Returns:
            True if restarted successfully, False otherwise

        Raises:
            BenchOperationException: If service not running or restart fails
        """
        if not docker_client_obj:
            docker_client_obj = self.docker_client

        try:
            all_statuses = docker_client_obj.compose.get_all_services_status()
            service_running = any(
                status["Service"] == service and status["State"] == "running" for status in all_statuses
            )
        except DockerException:
            service_running = False

        if not service_running:
            self.output.display_error(text=f"Service [blue]{service}[/blue] not running.")
            return False

        if force:
            stop_command = "supervisorctl -c /opt/user/supervisord.conf stop all"
            start_command = "supervisorctl -c /opt/user/supervisord.conf start all"
            try:
                docker_client_obj.compose.exec(service=service, user="frappe", command=stop_command, stream=False)
                docker_client_obj.compose.exec(service=service, user="frappe", command=start_command, stream=False)
            except DockerException as e:
                raise BenchOperationException(
                    self.bench_name,
                    message=f"Failed to force restart supervisor for {service} service: {e!s}",
                )
        else:
            restart_supervisor_command = "supervisorctl -c /opt/user/supervisord.conf restart all"
            try:
                docker_client_obj.compose.exec(
                    service=service,
                    user="frappe",
                    command=restart_supervisor_command,
                    stream=False,
                )
            except DockerException as e:
                raise BenchOperationException(
                    self.bench_name,
                    message=f"Failed to restart supervisor for {service} service: {e!s}",
                )

        # Verify supervisor socket was created after restart
        socket_path = f"/fm-sockets/{service}.sock"
        for _ in range(timeout):
            try:
                self.docker_client.compose.exec(
                    service=service,
                    user="frappe",
                    command=f"test -e {socket_path}",
                    stream=False,
                )
                return True
            except DockerException:
                time.sleep(interval)

        # Socket not found, but don't fail - it might be dev mode where socket isn't used
        self.output.warning(
            f"Supervisor socket {socket_path} not found after restart, but services may still be running",
        )
        return True

    def _run_frappe_command(self, command: str) -> None:
        """
        Run a command in the frappe service.

        Args:
            command: Command to execute

        Raises:
            DockerException: If command fails
        """
        try:
            self.docker_client.compose.exec("frappe", command, user="frappe", stream=False)
        except DockerException as e:
            from frappe_manager.site_manager.exceptions import BenchException

            raise BenchException("frappe", f"Failed to run {command} in frappe service.")

    def setup_supervisor(self, bench_path, force: bool = False, use_run: bool = False) -> None:
        """
        Set up supervisor configuration for the bench.

        Generates supervisor.conf and splits it into individual service configs.

        Args:
            bench_path: Path to the bench directory
            force: Force regeneration even if config exists
            use_run: If True, use 'docker compose run --rm' instead of 'exec'

        Raises:
            BenchOperationException: If supervisor setup fails

        Example:
            >>> supervisor.setup_supervisor(Path("/path/to/bench"), force=True)
        """
        from pathlib import Path

        frappe_bench_dir = bench_path / "workspace" / "frappe-bench"
        config_dir_path: Path = frappe_bench_dir / "config"
        supervisor_conf_path: Path = config_dir_path / "supervisor.conf"
        bench_cli_cmd = ["/usr/local/bin/bench"]

        self.output.change_head("Checking supervisor configuration")
        if not supervisor_conf_path.exists() or force:
            self.output.change_head("Configuring supervisor configs")

            bench_setup_supervisor_command = bench_cli_cmd + [
                "setup supervisor --skip-redis --skip-supervisord --yes --user frappe",
            ]

            bench_setup_supervisor_command = " ".join(bench_setup_supervisor_command)
            bench_setup_supervisor_exception = BenchOperationException(
                self.bench_name,
                "Failed to configure supervisor.",
            )

            try:
                if use_run:
                    run_command = f"-c 'cd /workspace/frappe-bench && {bench_setup_supervisor_command}'"
                    output = self.docker_client.compose.run(
                        service="frappe",
                        command=run_command,
                        entrypoint="/bin/bash",
                        user="frappe",
                        rm=True,
                        stream=False,
                    )
                else:
                    command = f"/bin/bash -c '{bench_setup_supervisor_command}'"
                    output = self.docker_client.compose.exec(
                        service="frappe",
                        command=command,
                        user="frappe",
                        workdir="/workspace/frappe-bench",
                        stream=False,
                    )
            except DockerException as e:
                bench_setup_supervisor_exception.set_output(e.output)
                raise bench_setup_supervisor_exception

            self.split_supervisor_config(bench_path)
            self.output.print("Configured supervisor configs")

    def split_supervisor_config(self, bench_path) -> None:
        """
        Split supervisor.conf into individual service configuration files.

        This method reads the monolithic supervisor.conf and splits it into
        separate files for each service (web, workers, etc.). It also handles
        symlinks and adjusts worker counts based on CPU.

        Args:
            bench_path: Path to the bench directory

        Example:
            >>> supervisor.split_supervisor_config(Path("/path/to/bench"))
        """
        import configparser
        import os
        import re
        from pathlib import Path

        frappe_bench_dir = bench_path / "workspace" / "frappe-bench"
        supervisor_conf_path: Path = frappe_bench_dir / "config" / "supervisor.conf"
        config = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
        config.read_string(supervisor_conf_path.read_text())

        handle_symlink_frappe_dir = False

        if frappe_bench_dir.is_symlink():
            handle_symlink_frappe_dir = True
            symlink_target = str(frappe_bench_dir.readlink())
            symlink_name = frappe_bench_dir.name

        for section_name in config.sections():
            if "group:" not in section_name:
                section_config = configparser.ConfigParser(interpolation=None)
                section_config.add_section(section_name)
                for key, value in config.items(section_name):
                    if handle_symlink_frappe_dir:
                        to_replace = str(frappe_bench_dir.readlink())

                        if to_replace in value:
                            value = value.replace(to_replace, frappe_bench_dir.name)

                    if "frappe-web" in section_name:
                        if key == "command":
                            value = value.replace("127.0.0.1:80", "0.0.0.0:80")
                            cpu_count = os.cpu_count() or 2
                            workers = (cpu_count * 2) + 1
                            value = re.sub(r"-w\s+\d+", f"-w {workers}", value)

                    if "node-socketio" in section_name:
                        if key == "command":
                            value = re.sub(r"\S+/node\s+", "/workspace/.fnm/aliases/default/bin/node ", value)

                    section_config.set(section_name, key, value)

                section_name_delimeter = "-frappe-"

                if "-node-" in section_name:
                    section_name_delimeter = "-node-"

                file_name_prefix = section_name.split(section_name_delimeter)[-1]
                file_name = file_name_prefix + ".fm.supervisor.conf"
                if "worker" in section_name:
                    file_name = file_name_prefix + ".workers.fm.supervisor.conf"

                new_file: Path = supervisor_conf_path.parent / file_name
                with open(new_file, "w") as section_file:
                    section_config.write(section_file)

                self.logger.info(f"Split supervisor conf {section_name} => {file_name}")
