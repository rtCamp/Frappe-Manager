"""
BenchSupervisor - Supervisor Process Management Module

This module handles Supervisor process management for bench services.
Extracted from the monolithic Bench class for better separation of concerns.
"""

import time
import json
import multiprocessing
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
        import psutil

        cpus = multiprocessing.cpu_count()
        ram_gb = psutil.virtual_memory().total / (1024**3)
        ram_based = max(1, int(ram_gb * 1024 / 256))
        return min(cpus, ram_based)

    def _get_gunicorn_threads(self) -> int:
        cpus = multiprocessing.cpu_count()
        return max(2, min(cpus, 4))

    def _get_default_max_requests(self, workers: int) -> int:
        return 1000

    def _compute_max_requests_jitter(self, max_requests: int) -> int:
        return int(max_requests * 0.1)

    def _can_enable_multi_queue_consumption(self, bench_path) -> bool:
        return True

    def generate_supervisor_config(self, bench_path, user="frappe", skip_redis=True) -> tuple[str, dict]:
        from pathlib import Path

        host_bench_dir = Path(bench_path).resolve() / "workspace" / "frappe-bench"
        common_site_config_path = host_bench_dir / "sites" / COMMON_SITE_CONFIG_FILE

        config = {}
        if common_site_config_path.exists():
            try:
                config = json.loads(common_site_config_path.read_text())
            except Exception:
                pass

        web_worker_count = config.get("gunicorn_workers", self._get_gunicorn_workers())
        max_requests = config.get("gunicorn_max_requests", self._get_default_max_requests(web_worker_count))
        # gthread worker class: each worker handles multiple concurrent requests via threads.
        # Frappe is IO-bound (DB/Redis heavy) and its concurrency_limiter explicitly reads
        # --threads from the gunicorn master cmdline, so gthread is the intended worker type.
        # Default 2 threads per worker; overridable via common_site_config.json.
        gunicorn_threads = config.get("gunicorn_threads", self._get_gunicorn_threads())

        def _coerce_positive_int(value, fallback: int) -> int:
            """Coerce a JSON-sourced knob to a positive int, falling back on bad input.

            common_site_config.json is user-editable, so a stringified or negative value
            shouldn't be allowed to land in the generated wrapper / supervisor config.
            """
            try:
                coerced = int(value)
            except (TypeError, ValueError):
                return fallback
            return coerced if coerced > 0 else fallback

        http_timeout = _coerce_positive_int(config.get("http_timeout"), 120)
        # --graceful-timeout caps how long an in-flight request has to drain when a
        # worker is recycled (e.g. on --max-requests). Defaulting it to http_timeout
        # keeps it consistent with --timeout (-t); operators can override either
        # independently via common_site_config.json.
        gunicorn_graceful_timeout = _coerce_positive_int(
            config.get("gunicorn_graceful_timeout"), http_timeout
        )
        # Supervisor must wait longer than gunicorn's graceful drain window before
        # SIGKILL'ing the master, otherwise an increased --graceful-timeout has no
        # effect (the template comment on supervisor.conf.tmpl explicitly calls this
        # out). +10s of headroom covers the master's own post-drain reaping.
        supervisor_web_stopwaitsecs = gunicorn_graceful_timeout + 10

        context = {
            "bench_dir": CONTAINER_BENCH_DIR,
            "sites_dir": f"{CONTAINER_BENCH_DIR}/sites",
            "user": user,
            "use_rq": True,
            "http_timeout": http_timeout,
            "node": "/workspace/frappe-bench/.fnm/aliases/default/bin/node",
            "webserver_port": config.get("webserver_port", 80),
            "gunicorn_workers": web_worker_count,
            "gunicorn_max_requests": max_requests,
            "gunicorn_max_requests_jitter": self._compute_max_requests_jitter(max_requests),
            "gunicorn_threads": gunicorn_threads,
            "gunicorn_graceful_timeout": gunicorn_graceful_timeout,
            "supervisor_web_stopwaitsecs": supervisor_web_stopwaitsecs,
            "bench_name": "frappe-bench",
            "background_workers": config.get("background_workers") or 1,
            "bench_cmd": "/opt/user/.bin/bench",
            "workers": config.get("workers", {}),
            "multi_queue_consumption": self._can_enable_multi_queue_consumption(bench_path),
            "supervisor_startretries": 10,
        }

        template_path = get_template_path("supervisor.conf.tmpl")
        return Template(template_path.read_text()).render(**context), context

    def setup_supervisor(self, bench_path, force: bool = False, use_run: bool = False) -> None:
        import configparser
        import io
        from pathlib import Path

        bench_dir = Path(bench_path).resolve() / "workspace" / "frappe-bench"
        config_dir = bench_dir / "config"

        self.output.change_head("Checking supervisor configuration")

        fm_confs_exist = any(config_dir.glob("*.fm.supervisor.conf")) if config_dir.exists() else False
        if fm_confs_exist and not force:
            return

        self.output.change_head("Configuring supervisor configs")
        try:
            rendered, context = self.generate_supervisor_config(bench_path)
        except Exception as e:
            raise BenchOperationException(self.bench_name, f"Failed to configure supervisor: {e}")

        config_dir.mkdir(parents=True, exist_ok=True)
        parsed = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
        parsed.read_string(rendered)
        self._write_split_configs(parsed, config_dir)
        self._write_gunicorn_wrapper(config_dir, context)
        if self.config.newrelic_enabled and self.config.newrelic_license_key:
            self._write_newrelic_config(config_dir)
        self.output.print("Configured supervisor configs")

    def setup_newrelic(self, bench_path) -> None:
        from pathlib import Path

        bench_dir = Path(bench_path).resolve() / "workspace" / "frappe-bench"
        config_dir = bench_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        self.output.change_head("Configuring supervisor configs")

        try:
            _, context = self.generate_supervisor_config(bench_path)
        except Exception as e:
            raise BenchOperationException(self.bench_name, f"Failed to read bench config for NewRelic setup: {e}")

        self._write_gunicorn_wrapper(config_dir, context)

        if self.config.newrelic_enabled and self.config.newrelic_license_key:
            self._write_newrelic_config(config_dir)

        self.output.print("Configured supervisor configs")

    def _write_split_configs(self, config, config_dir) -> None:
        import configparser
        import io
        from pathlib import Path

        for section_name in config.sections():
            if section_name.startswith("group:"):
                continue

            section_config = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
            section_config.add_section(section_name)
            for key, value in config.items(section_name):
                section_config.set(section_name, key, value)

            delimiter = "-node-" if "-node-" in section_name else "-frappe-"
            file_name_prefix = section_name.split(delimiter)[-1]
            file_name = (
                file_name_prefix + ".workers.fm.supervisor.conf"
                if "worker" in section_name
                else file_name_prefix + ".fm.supervisor.conf"
            )

            buf = io.StringIO()
            section_config.write(buf)
            Path(config_dir / file_name).write_text(buf.getvalue())
            self.logger.info(f"Split supervisor conf {section_name} => {file_name}")

    def _write_newrelic_config(self, config_dir) -> None:
        import configparser
        import io
        from pathlib import Path

        cfg = configparser.RawConfigParser()

        cfg.add_section("newrelic")
        cfg.set("newrelic", "license_key", self.config.newrelic_license_key or "")
        cfg.set("newrelic", "app_name", f"Frappe - {self.bench_name}")
        cfg.set("newrelic", "monitor_mode", "true")
        cfg.set("newrelic", "high_security", "false")
        cfg.set("newrelic", "log_file", f"{CONTAINER_BENCH_DIR}/logs/newrelic-agent.log")
        cfg.set("newrelic", "log_level", "info")
        cfg.set("newrelic", "startup_timeout", "0.0")
        cfg.set("newrelic", "shutdown_timeout", "2.5")
        cfg.set("newrelic", "distributed_tracing.enabled", "true")
        cfg.set("newrelic", "span_events.max_samples_stored", "2000")
        cfg.set("newrelic", "datastore_tracer.instance_reporting.enabled", "true")
        cfg.set("newrelic", "datastore_tracer.database_name_reporting.enabled", "true")

        cfg.add_section("transaction_tracer")
        cfg.set("transaction_tracer", "enabled", "true")
        cfg.set("transaction_tracer", "transaction_threshold", "apdex_f")
        cfg.set("transaction_tracer", "record_sql", "obfuscated")
        cfg.set("transaction_tracer", "stack_trace_threshold", "0.5")
        cfg.set("transaction_tracer", "explain_enabled", "true")
        cfg.set("transaction_tracer", "explain_threshold", "0.5")
        cfg.set("transaction_tracer", "attributes.enabled", "true")
        cfg.set(
            "transaction_tracer",
            "attributes.include",
            "request.headers.x-frappe-request-id request.uri request.method",
        )
        cfg.set(
            "transaction_tracer",
            "attributes.exclude",
            "request.headers.authorization request.headers.cookie",
        )

        cfg.add_section("error_collector")
        cfg.set("error_collector", "enabled", "true")
        cfg.set("error_collector", "ignore_status_codes", "100-102 200-208 226 300-308 404")
        cfg.set(
            "error_collector",
            "expected_classes",
            (
                "frappe.exceptions.ValidationError "
                "frappe.exceptions.PermissionError "
                "frappe.exceptions.DoesNotExistError"
            ),
        )
        cfg.set("error_collector", "attributes.enabled", "true")
        cfg.set("error_collector", "max_event_samples_stored", "100")

        buf = io.StringIO()
        cfg.write(buf)
        Path(config_dir / "newrelic.ini").write_text(buf.getvalue())
        self.logger.info("Generated newrelic.ini")

    def _write_gunicorn_wrapper(self, config_dir, context: dict) -> None:
        from pathlib import Path

        gunicorn_args = (
            f"-b 0.0.0.0:{context['webserver_port']}"
            f" -w {context['gunicorn_workers']}"
            f" --worker-class=gthread"
            f" --threads {context['gunicorn_threads']}"
            f" --max-requests {context['gunicorn_max_requests']}"
            f" --max-requests-jitter {context['gunicorn_max_requests_jitter']}"
            f" -t {context['http_timeout']}"
            f" --graceful-timeout {context['gunicorn_graceful_timeout']}"
            f" frappe.app:application --preload"
        )

        template_path = get_template_path("fm-web-server.sh.tmpl")
        script = Template(template_path.read_text()).render(
            bench_dir=context["bench_dir"],
            gunicorn_args=gunicorn_args,
            bench_name=self.bench_name,
        )

        wrapper_path = Path(config_dir) / "fm-web-server.sh"
        wrapper_path.write_text(script)
        wrapper_path.chmod(0o755)
        self.logger.info("Generated gunicorn.sh wrapper")
