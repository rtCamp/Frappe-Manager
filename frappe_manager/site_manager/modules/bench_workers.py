"""
BenchWorkerCoordinator Module

Handles worker coordination for the bench including:
- Worker compose file synchronization
- Supervisor configuration backup and restore
- Worker startup checks
- Worker service restarts
"""

from typing import TYPE_CHECKING
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.docker import DockerException
from frappe_manager.site_manager.site_exceptions import BenchOperationException
from frappe_manager import SiteServicesEnum

if TYPE_CHECKING:
    from frappe_manager.site_manager.workers_manager.SiteWorker import BenchWorkers


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
