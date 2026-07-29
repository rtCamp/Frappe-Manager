"""
Bench Workers Module

Provides worker management for the bench including:
- Worker compose file generation and management (BenchWorkers)
- Worker coordination and lifecycle (BenchWorkerCoordinator)
- Supervisor configuration backup and restore
- Worker startup checks and service restarts
"""

from copy import deepcopy
from typing import TYPE_CHECKING

from frappe_manager import CLI_SERVICES_DIRECTORY, SiteServicesEnum
from frappe_manager.docker import ComposeFile, DockerClient, DockerException
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import WorkersConfig
from frappe_manager.site_manager.exceptions import (
    BenchOperationException,
    BenchWorkersSupervisorConfigurtionNotFoundError,
)
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.utils.helpers import get_container_name_prefix, get_current_fm_version
from frappe_manager.utils.network import get_proxy_ip_on_frontend
from frappe_manager.utils.site import is_default_worker

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Bench


# fmx ships inside the frappe image; used for the worker-cycle kill ladder.
FMX_BIN = "/opt/uv-tools/fmx/bin/fmx"


class BenchWorkers:
    """
    Manages worker compose file generation and configuration.

    Responsibilities:
    - Generate worker compose files based on supervisor configuration
    - Manage worker service definitions
    - Handle custom and default workers
    - Clean up worker compose files when no longer needed
    """

    def __init__(self, bench: "Bench", verbose: bool = True, output_handler: OutputHandler | None = None):
        """
        Initialize BenchWorkers.

        Args:
            bench: The Bench instance
            verbose: Whether to show verbose output
            output_handler: Handler for output operations
        """
        self.bench = bench
        self.compose_path = self.bench.path / "docker-compose.workers.yml"
        self.config_dir = self.bench.path / "workspace" / "frappe-bench" / "config"
        self.supervisor_config_path = self.config_dir / "supervisor.conf"
        self.output = output_handler or RichOutputHandler()
        self.compose_file_manager = ComposeFile(self.compose_path, template_name="docker-compose.workers.tmpl")
        self.docker_client = DockerClient(compose_file_path=self.compose_path, output=self.output)

    def get_expected_workers(
        self,
        include_default_workers: bool = True,
        include_custom_workers: bool = True,
    ) -> list[str]:
        """
        Get list of expected workers from supervisor configuration.

        Args:
            include_default_workers: Whether to include default workers (short, long)
            include_custom_workers: Whether to include custom workers

        Returns:
            Sorted list of worker service names

        Raises:
            BenchWorkersSupervisorConfigurtionNotFoundError: If no worker configs found
        """
        self.output.change_head("Checking workers info")

        workers_supervisor_conf_paths = []

        for file_path in self.config_dir.iterdir():
            file_path_abs = str(file_path.absolute())
            if file_path.is_file():
                if file_path_abs.endswith(".workers.fm.supervisor.conf"):
                    workers_supervisor_conf_paths.append(file_path)

        if len(workers_supervisor_conf_paths) == 0:
            raise BenchWorkersSupervisorConfigurtionNotFoundError(self.bench.name, str(self.config_dir))

        workers_expected_service_names = []

        for worker_name in workers_supervisor_conf_paths:
            worker_name = worker_name.name
            worker_name = worker_name.replace("frappe-bench-frappe-", "")
            worker_name = worker_name.replace(".workers.fm.supervisor.conf", "")

            if is_default_worker(worker_name):
                if include_default_workers:
                    workers_expected_service_names.append(worker_name)
            elif include_custom_workers:
                workers_expected_service_names.append(worker_name)

        workers_expected_service_names.sort()

        return workers_expected_service_names

    def is_new_workers_added(self, include_default_workers: bool = False) -> bool:
        """
        Check if worker configuration has changed.

        Args:
            include_default_workers: Whether to include default workers in comparison

        Returns:
            True if workers configuration matches expected, False otherwise
        """
        if not self.compose_file_manager.is_template_loaded:
            prev_workers = self.compose_file_manager.get_services_list()
            prev_workers.sort()
            expected_workers = self.get_expected_workers(include_default_workers=include_default_workers)

            # get custom workers from common_site_config.json
            common_site_config_data = self.bench.get_common_bench_config()

            custom_workers = common_site_config_data.get("workers") or {}
            if not isinstance(custom_workers, dict):
                # Malformed key: treat as changed so the regen path surfaces the
                # validation error instead of silently reporting "unchanged".
                return False
            expected_custom = {f"{name}-worker" for name in custom_workers}

            # A queue added to the config must appear in the compose...
            for worker in expected_custom:
                if worker not in prev_workers:
                    return False
            # ...and a queue removed from the config must disappear from it.
            for worker in prev_workers:
                if not is_default_worker(worker) and worker not in expected_custom:
                    return False
            return prev_workers == expected_workers

        return False

    def generate_compose(self, include_default_workers: bool = True, include_custom_workers: bool = True) -> bool:
        """
        Generate worker compose file from template.

        Args:
            include_default_workers: Whether to include default workers
            include_custom_workers: Whether to include custom workers

        Returns:
            True if workers were configured and need starting, False otherwise
        """
        self.output.change_head("Generating workers compose configuration")

        prev_services: list[str] = []
        if not self.compose_path.exists():
            self.output.print("Workers compose not present. Generating new configuration..")
        else:
            self.output.print("Workers configuration changed. Recreating compose..")
            try:
                prev_services = self.compose_file_manager.get_services_list()
            except Exception:
                prev_services = []

        self.compose_file_manager.yml = self.compose_file_manager.load_template()

        template_worker_config = self.compose_file_manager.yml["services"]["worker-name"]
        del self.compose_file_manager.yml["services"]["worker-name"]

        workers_expected_service_names = self.get_expected_workers(
            include_default_workers=include_default_workers,
            include_custom_workers=include_custom_workers,
        )

        # Remove dropped workers while the OLD compose file still defines them.
        # NEVER use `up/down --remove-orphans` for this: all bench compose files
        # share one directory and therefore one compose project, so orphan
        # removal on the workers file removes every main-stack container too.
        removed_services = sorted(set(prev_services) - set(workers_expected_service_names))
        if removed_services:
            self.output.print(f"Removing dropped worker service(s): {', '.join(removed_services)}")
            try:
                self.docker_client.compose.rm(services=removed_services, stop=True, force=True, stream=False)
            except DockerException as e:
                self.output.warning(f"Could not remove dropped worker container(s) ({e}); remove manually")

        if len(workers_expected_service_names) > 0:
            import os

            # Detect the global proxy IP live from Docker so it stays correct
            # even after the proxy is recreated.
            proxy_ip = get_proxy_ip_on_frontend()

            extra_hosts = None
            if proxy_ip:
                all_domains = [self.bench.name]
                if self.bench.bench_config.alias_domains:
                    all_domains.extend(self.bench.bench_config.alias_domains)
                extra_hosts = [f"{domain}:{proxy_ip}" for domain in all_domains]

            # For dev SSL, workers need the CA cert mounted so outbound HTTPS
            # requests trust the self-signed dev certificate. Resolve this once.
            has_dev_ssl = any(
                cert.ssl_type == SUPPORTED_SSL_TYPES.dev
                for cert in self.bench.bench_config.ssl_certificates
            )
            ca_host = None
            if has_dev_ssl:
                candidate = CLI_SERVICES_DIRECTORY / "nginx-proxy" / "ssl" / "dev" / "ca" / "rootCA.pem"
                if candidate.exists():
                    ca_host = candidate
            ca_container = "/etc/ssl/certs/fm-dev-ca.pem"

            from frappe_manager.site_manager.modules.compose_shape import bind_strings, worker_service_specs

            # Mode shape (image + binds) is projected per worker from bench_config via
            # compose_shape -- same specs deploy re-pins use, so every regen
            # (create/update/restart/reconfigure) yields the correct runtime shape.
            shape_specs = {
                s.name: s for s in worker_service_specs(self.bench.bench_config, workers_expected_service_names)
            }
            for worker in workers_expected_service_names:
                worker_config = deepcopy(template_worker_config)
                spec = shape_specs.get(worker)
                if spec:
                    if spec.image:
                        worker_config["image"] = spec.image
                    worker_config["volumes"] = ["fm-sockets:/fm-sockets", *bind_strings(spec)]
                worker_config["environment"]["USERID"] = os.getuid()
                worker_config["environment"]["USERGROUP"] = os.getgid()
                worker_config["environment"]["WORKER_NAME"] = worker
                if extra_hosts:
                    worker_config["extra_hosts"] = extra_hosts
                if ca_host:
                    worker_config.setdefault("volumes", []).append(f"{ca_host}:{ca_container}:ro")
                    worker_config["environment"]["NODE_EXTRA_CA_CERTS"] = ca_container
                    worker_config["environment"]["REQUESTS_CA_BUNDLE"] = ca_container

                self.compose_file_manager.yml["services"][worker] = worker_config

            self.compose_file_manager.with_prefix(
                get_container_name_prefix(self.bench.name),
                "site-network",
            ).with_version(get_current_fm_version()).with_restart(self.bench.bench_config.restart_policy.value).commit()

            self.output.print(f"{' '.join(workers_expected_service_names)} configurations generated")
            return True

        if self.compose_file_manager.exists():
            self.output.print("No workers found, cleaning up existing configuration")
            # Plain down (NO remove_orphans: shared compose project, see above).
            self.docker_client.compose.down(volumes=False, timeout=5, stream=True)
            self.compose_file_manager.compose_path.unlink()

        return False


class BenchWorkerCoordinator:
    """
    Coordinates worker processes for a bench.

    Responsibilities:
    - Sync worker compose files
    - Backup and restore worker supervisor configs
    - Ensure workers are running
    - Restart worker services
    """

    def __init__(
        self,
        bench_name: str,
        workers: "BenchWorkers",
        supervisor,
        bench_path,
        restart_supervisor_service_fn,
        is_running_fn,
        docker_ops=None,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchWorkerCoordinator module.

        Args:
            bench_name: Name of the bench
            workers: BenchWorkers instance (from workers_manager)
            supervisor: BenchSupervisor instance for supervisor setup
            bench_path: Path to bench directory
            restart_supervisor_service_fn: Callable to restart supervisor service
            is_running_fn: Callable to check if bench is running
            docker_ops: BenchDockerOps instance for docker operations
            output_handler: Handler for output operations
        """
        self.bench_name = bench_name
        self.workers = workers
        self.supervisor = supervisor
        self.bench_path = bench_path
        self.restart_supervisor_service = restart_supervisor_service_fn
        self.is_running = is_running_fn
        self.docker_ops = docker_ops
        self.output = output_handler or RichOutputHandler()

    def sync_workers_compose(
        self,
        force_recreate: bool = False,
        setup_supervisor: bool = True,
        include_default_workers: bool = True,
        include_custom_workers: bool = True,
        start: bool = True,
    ):
        """
        Synchronize workers compose file and optionally setup supervisor.

        Args:
            force_recreate: Force recreate containers
            setup_supervisor: Whether to setup supervisor
            include_default_workers: Include default workers
            include_custom_workers: Include custom workers
        """
        if setup_supervisor:
            workers_backup_manager = self.backup_workers_supervisor_conf()
            try:
                self.supervisor.setup_supervisor(self.bench_path, force=True)
            except BenchOperationException:
                self.backup_restore_workers_supervisor(workers_backup_manager)
                # A failed regen (e.g. invalid common_site_config workers entry)
                # must fail the sync loudly, not fall through as "unchanged".
                raise

        are_workers_not_changed = self.workers.is_new_workers_added(include_default_workers=include_default_workers)

        if are_workers_not_changed:
            self.output.print("Workers configuration remains unchanged")
            return

        start_required = self.workers.generate_compose(
            include_default_workers=include_default_workers,
            include_custom_workers=include_custom_workers,
        )

        if start_required and start:
            self.workers.docker_client.compose.up(
                services=[],
                detach=True,
                pull="never",
                force_recreate=force_recreate,
            )

    def backup_restore_workers_supervisor(self, backup_manager: BackupManager):
        """
        Restore workers supervisor configuration from backup.

        Args:
            backup_manager: BackupManager instance containing backups
        """
        self.output.print("Rolling back to previous workers configuration")
        for backup in backup_manager.backups:
            backup_manager.restore(backup, force=True)

    def backup_workers_supervisor_conf(self) -> BackupManager:
        """
        Backup workers supervisor configuration files.

        Returns:
            BackupManager: Manager containing backed up files
        """
        backup_workers_manager = BackupManager(name="workers", backup_group_name="workers")
        backup_workers_manager.backup(self.workers.supervisor_config_path, bench_name=self.bench_name)

        if self.workers.supervisor_config_path.exists():
            for file_path in self.workers.config_dir.iterdir():
                file_path_abs = str(file_path.absolute())
                if not file_path.is_file():
                    continue
                if file_path_abs.endswith(".fm.supervisor.conf"):
                    from_path = file_path
                    backup_workers_manager.backup(from_path, bench_name=self.bench_name)
                    file_path.unlink()
        return backup_workers_manager

    def regenerate_workers_supervisor_conf(self):
        """Regenerate workers supervisor configuration by backing up existing config."""
        self.backup_workers_supervisor_conf()

    def ensure_workers_running_if_available(self):
        """Ensure workers are running if compose file exists and bench is running."""
        if self.workers.compose_file_manager.exists():
            services = self.workers.compose_file_manager.get_services_list()
            containers = self.workers.compose_file_manager.get_container_names().values()
            try:
                all_statuses = self.workers.docker_client.compose.get_all_services_status()
                running_statuses = {
                    status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
                }
                workers_running = all(running_statuses.get(s) == "running" for s in services)
            except DockerException:
                workers_running = False

            if not workers_running:
                if self.is_running():
                    self.workers.docker_client.compose.up(
                        services=[],
                        detach=True,
                        pull="never",
                        force_recreate=False,
                    )

    def _fmx_cycle(self, service: str, docker_client_obj) -> None:
        """Restart this container's supervisor programs via fmx's kill ladder
        (SIGUSR1, then stopProcess after [workers].kill_timeout). Drain/
        suspend is handled globally by the caller, so fmx's own drain is
        disabled to avoid a second wait."""
        wc = self.workers.bench.bench_config.workers or WorkersConfig()
        cmd = (
            f"{FMX_BIN} restart --no-drain-workers --wait"
            f" --worker-kill-timeout {wc.kill_timeout}"
            f" --worker-kill-poll {wc.kill_poll}"
        )
        docker_client_obj.compose.exec(service=service, user="frappe", command=cmd, stream=False)

    def _cycle_supervisor_programs(self, service: str, docker_client_obj=None) -> None:
        """Non-force worker cycle: fmx's SIGUSR1 ladder, falling back to
        supervisorctl on images that predate fmx."""
        try:
            self._fmx_cycle(service, docker_client_obj or self.docker_ops.docker_client)
            self.output.print(f"Restarted supervisor processes - {service}")
        except Exception:
            self.output.warning(f"fmx restart unavailable in {service} (old image?); falling back to supervisorctl")
            is_restarted = self.restart_supervisor_service(service, docker_client_obj=docker_client_obj, force=False)
            if is_restarted:
                self.output.print(f"Restarted supervisor processes - {service}")

    def restart_workers_containers_services(self, use_container_restart: bool = False, force: bool = False):
        """
        Restart workers and schedule containers.

        Args:
            use_container_restart: If True, restart entire containers. If False, restart supervisor processes.
            force: If True, use aggressive restart (timeout=0 for container, stop+start for supervisor).
        """
        scheduler_service = [SiteServicesEnum.schedule.value]

        if use_container_restart:
            self.docker_ops.restart_services(scheduler_service, force=force)
        else:
            for service in scheduler_service:
                self.output.change_head(f"Restarting worker service - {service}")
                if force:
                    is_restarted = self.restart_supervisor_service(service, force=force)
                    if is_restarted:
                        self.output.print(f"Stopped and started supervisor processes - {service}")
                else:
                    self._cycle_supervisor_programs(service)

        worker_services = self.workers.compose_file_manager.get_services_list()
        for service in worker_services:
            self.output.change_head(f"Restarting worker service - {service}")

            if use_container_restart:
                timeout = 0 if force else 100
                self.workers.docker_client.compose.restart(services=[service], timeout=timeout)
                action = "Force restarted" if force else "Restarted"
                self.output.print(f"{action} container - {service}")
            elif force:
                is_restarted = self.restart_supervisor_service(
                    service,
                    docker_client_obj=self.workers.docker_client,
                    force=force,
                )
                if is_restarted:
                    self.output.print(f"Stopped and started supervisor processes - {service}")
            else:
                self._cycle_supervisor_programs(service, docker_client_obj=self.workers.docker_client)
