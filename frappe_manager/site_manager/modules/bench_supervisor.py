"""
BenchSupervisor - Supervisor Process Management Module

This module handles Supervisor process management for bench services.
Extracted from the monolithic Bench class for better separation of concerns.
"""

import time
import json
import os
import multiprocessing
import random
from jinja2 import Template
from frappe_manager.utils.helpers import get_template_path
from frappe_manager.docker import DockerClient, DockerException
from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.exceptions import BenchOperationException

CONTAINER_BENCH_DIR = "/workspace/frappe-bench"
COMMON_SITE_CONFIG_FILE = "common_site_config.json"


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

    def _get_gunicorn_workers(self) -> int:
        return (multiprocessing.cpu_count() * 2) + 1

    def _get_default_max_requests(self, workers: int) -> int:
        return 1000

    def _compute_max_requests_jitter(self, max_requests: int) -> int:
        return int(max_requests * 0.1)

    def _can_enable_multi_queue_consumption(self, bench_path) -> bool:
        return True

    def generate_supervisor_config(self, bench_path, user="frappe", skip_redis=True):
        """Generate supervisor config for respective bench path using FM template."""
        from pathlib import Path

        # Host paths for file operations
        host_bench_dir = Path(bench_path).resolve() / "workspace" / "frappe-bench"
        common_site_config_path = host_bench_dir / "sites" / COMMON_SITE_CONFIG_FILE

        # Container paths for config content
        container_bench_dir = CONTAINER_BENCH_DIR

        config = {}
        if common_site_config_path.exists():
            try:
                config = json.loads(common_site_config_path.read_text())
            except Exception:
                pass

        web_worker_count = config.get("gunicorn_workers", self._get_gunicorn_workers())
        max_requests = config.get("gunicorn_max_requests", self._get_default_max_requests(web_worker_count))

        context = {
            "bench_dir": container_bench_dir,
            "sites_dir": f"{container_bench_dir}/sites",
            "user": user,
            "use_rq": True,
            "http_timeout": config.get("http_timeout", 120),
            "node": "/workspace/frappe-bench/.fnm/aliases/default/bin/node",
            "webserver_port": config.get("webserver_port", 80),
            "gunicorn_workers": web_worker_count,
            "gunicorn_max_requests": max_requests,
            "gunicorn_max_requests_jitter": self._compute_max_requests_jitter(max_requests),
            "bench_name": "frappe-bench",
            "background_workers": config.get("background_workers") or 1,
            "bench_cmd": "/opt/user/.bin/bench",
            "workers": config.get("workers", {}),
            "multi_queue_consumption": self._can_enable_multi_queue_consumption(bench_path),
            "supervisor_startretries": 10,
        }

        template_path = get_template_path("supervisor.conf.tmpl")
        template = Template(template_path.read_text())
        rendered_config = template.render(**context)

        # Write config to host path
        config_dir = host_bench_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        conf_path = config_dir / "supervisor.conf"
        conf_path.write_text(rendered_config)

        return conf_path

    def setup_supervisor(self, bench_path, force: bool = False, use_run: bool = False) -> None:
        """
        Set up supervisor configuration for the bench using native FM implementation.

        Args:
            bench_path: Path to the bench directory
            force: Force regeneration even if config exists
            use_run: Unused but kept for compatibility
        """
        from pathlib import Path

        bench_dir = Path(bench_path).resolve() / "workspace" / "frappe-bench"
        config_dir_path = bench_dir / "config"
        supervisor_conf_path = config_dir_path / "supervisor.conf"

        self.output.change_head("Checking supervisor configuration")
        if not supervisor_conf_path.exists() or force:
            self.output.change_head("Configuring supervisor configs")

            try:
                self.generate_supervisor_config(bench_path, skip_redis=True)
            except Exception as e:
                raise BenchOperationException(self.bench_name, f"Failed to configure supervisor: {e}")

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
                            value = re.sub(
                                r"\S+/node\s+", "/workspace/frappe-bench/.fnm/aliases/default/bin/node ", value
                            )

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
