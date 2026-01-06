"""
Bench Workers Module

Provides worker management for the bench including:
- Worker compose file generation and management (BenchWorkers)
- Worker coordination and lifecycle (BenchWorkerCoordinator)
- Supervisor configuration backup and restore
- Worker startup checks and service restarts
"""

from copy import deepcopy
from pathlib import Path
from typing import List, TYPE_CHECKING
from frappe_manager.docker import ComposeFile, DockerClient, DockerException
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.site_manager.site_exceptions import (
    BenchOperationException,
    BenchWorkersSupervisorConfigurtionNotFoundError
)
from frappe_manager.utils.helpers import get_container_name_prefix, get_current_fm_version
from frappe_manager.utils.site import is_default_worker
from frappe_manager import SiteServicesEnum

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Bench


class BenchWorkers:
    """
    Manages worker compose file generation and configuration.
    
    Responsibilities:
    - Generate worker compose files based on supervisor configuration
    - Manage worker service definitions
    - Handle custom and default workers
    - Clean up worker compose files when no longer needed
    """
    
    def __init__(self, bench: 'Bench', verbose: bool = True):
        """
        Initialize BenchWorkers.
        
        Args:
            bench: The Bench instance
            verbose: Whether to show verbose output
        """
        self.bench = bench
        self.compose_path = self.bench.path / "docker-compose.workers.yml"
        self.config_dir = self.bench.path / "workspace" / "frappe-bench" / "config"
        self.supervisor_config_path = self.config_dir / "supervisor.conf"
        self.quiet = not verbose
        self.compose_file_manager = ComposeFile(self.compose_path, template_name='docker-compose.workers.tmpl')
        self.docker_client = DockerClient(compose_file_path=self.compose_path)

    def get_expected_workers(self, include_default_workers: bool = True, include_custom_workers: bool = True) -> List[str]:
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
        richprint.change_head("Checking workers info.")

        workers_supervisor_conf_paths = []

        for file_path in self.config_dir.iterdir():
            file_path_abs = str(file_path.absolute())
            if file_path.is_file():
                if file_path_abs.endswith(".workers.fm.supervisor.conf"):
                    workers_supervisor_conf_paths.append(file_path)

        if len(workers_supervisor_conf_paths) == 0:
            raise BenchWorkersSupervisorConfigurtionNotFoundError(self.bench.name, self.config_dir)

        workers_expected_service_names = []

        for worker_name in workers_supervisor_conf_paths:
            worker_name = worker_name.name
            worker_name = worker_name.replace("frappe-bench-frappe-", "")
            worker_name = worker_name.replace(".workers.fm.supervisor.conf", "")

            if is_default_worker(worker_name):
                if include_default_workers:
                    workers_expected_service_names.append(worker_name)
            else:
                if include_custom_workers:
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

            if 'workers' in common_site_config_data:
                custom_workers: List[str] = common_site_config_data['workers'].keys()
                for worker in custom_workers:
                    worker = f'{worker}-worker'
                    if worker not in prev_workers:
                        return False
            return prev_workers == expected_workers

        else:
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
        richprint.change_head("Generating workers compose configuration")

        if not self.compose_path.exists():
            richprint.print("Workers compose not present. Generating new configuration...")
        else:
            richprint.print("Workers configuration changed. Recreating compose...")

        # create compose file for workers
        self.compose_file_manager.yml = self.compose_file_manager.load_template()
        richprint.print("Loaded compose template")

        template_worker_config = self.compose_file_manager.yml["services"]["worker-name"]
        del self.compose_file_manager.yml["services"]["worker-name"]

        workers_expected_service_names = self.get_expected_workers(
            include_default_workers=include_default_workers,
            include_custom_workers=include_custom_workers
        )

        if len(workers_expected_service_names) > 0:
            richprint.print(f"Configuring {len(workers_expected_service_names)} workers")
            import os
            for worker in workers_expected_service_names:
                worker_config = deepcopy(template_worker_config)

                # setting environments
                worker_config["environment"]["USERID"] = os.getuid()
                worker_config["environment"]["USERGROUP"] = os.getgid()
                worker_config["environment"]["WORKER_NAME"] = worker

                self.compose_file_manager.yml["services"][worker] = worker_config

            # Use new with_prefix and with_version methods to set all configurations atomically
            self.compose_file_manager \
                .with_prefix(get_container_name_prefix(self.bench.name), 'site-network') \
                .with_version(get_current_fm_version()) \
                .commit()
            richprint.print("Workers configuration generated successfully")
            return True

        else:
            if self.compose_file_manager.exists():
                richprint.print("No workers found, cleaning up existing configuration")
                output = self.docker_client.compose.down(remove_orphans=True, volumes=False, timeout=5, stream=True)
                if not self.quiet:
                    richprint.live_lines(output, padding=(0, 0, 0, 2))
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
        quiet: bool = False,
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
            quiet: Whether to suppress output
        """
        self.bench_name = bench_name
        self.workers = workers
        self.supervisor = supervisor
        self.bench_path = bench_path
        self.restart_supervisor_service = restart_supervisor_service_fn
        self.is_running = is_running_fn
        self.quiet = quiet

    def sync_workers_compose(
        self,
        force_recreate: bool = False,
        setup_supervisor: bool = True,
        include_default_workers: bool = True,
        include_custom_workers: bool = True
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
            except BenchOperationException as e:
                self.backup_restore_workers_supervisor(workers_backup_manager)

        are_workers_not_changed = self.workers.is_new_workers_added(
            include_default_workers=include_default_workers
        )

        if are_workers_not_changed:
            richprint.print("Workers configuration remains unchanged.")
            return

        start_required = self.workers.generate_compose(
            include_default_workers=include_default_workers,
            include_custom_workers=include_custom_workers
        )

        if start_required:
            output = self.workers.docker_client.compose.up(
                services=[],
                detach=True,
                pull="never",
                force_recreate=force_recreate,
                stream=self.quiet
            )
            if self.quiet:
                richprint.live_lines(output, padding=(0, 0, 0, 2))

    def backup_restore_workers_supervisor(self, backup_manager: BackupManager):
        """
        Restore workers supervisor configuration from backup.
        
        Args:
            backup_manager: BackupManager instance containing backups
        """
        richprint.print("Rolling back to previous workers configuration.")
        for backup in backup_manager.backups:
            backup_manager.restore(backup, force=True)

    def backup_workers_supervisor_conf(self) -> BackupManager:
        """
        Backup workers supervisor configuration files.
        
        Returns:
            BackupManager: Manager containing backed up files
        """
        backup_workers_manager = BackupManager(name='workers', backup_group_name='workers')
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
            # Check if workers are running
            services = self.workers.compose_file_manager.get_services_list()
            containers = self.workers.compose_file_manager.get_container_names().values()
            try:
                all_statuses = self.workers.docker_client.compose.get_all_services_status()
                running_statuses = {
                    status["Service"]: status["State"]
                    for status in all_statuses
                    if status.get("Name") in containers
                }
                workers_running = all(running_statuses.get(s) == "running" for s in services)
            except DockerException:
                workers_running = False
            
            if not workers_running:
                if self.is_running():
                    output = self.workers.docker_client.compose.up(
                        services=[],
                        detach=True,
                        pull="never",
                        force_recreate=False,
                        stream=self.quiet
                    )
                    if self.quiet:
                        richprint.live_lines(output, padding=(0, 0, 0, 2))

    def restart_workers_containers_services(self):
        """Restart workers and schedule containers."""
        # Restart scheduler
        worker_services = [SiteServicesEnum.schedule.value]

        for service in worker_services:
            richprint.change_head(f"Restarting worker service - {service}")
            is_restarted = self.restart_supervisor_service(service)
            if is_restarted:
                richprint.print(f"Restarted worker services - {service}")

        # Restart worker containers
        worker_services = self.workers.compose_file_manager.get_services_list()
        for service in worker_services:
            richprint.change_head(f"Restarting worker service - {service}")
            is_restarted = self.restart_supervisor_service(
                service,
                docker_client_obj=self.workers.docker_client
            )
            if is_restarted:
                richprint.print(f"Restarted worker services - {service}")
