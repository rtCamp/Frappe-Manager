"""
BenchSupervisor - Supervisor Process Management Module

This module handles Supervisor process management for bench services.
Extracted from the monolithic Bench class for better separation of concerns.
"""

import time
from typing import Optional

from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker import DockerClient, DockerException
from frappe_manager.logger import log
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.site_manager.site_exceptions import BenchOperationException


class BenchSupervisor:
    """Manages Supervisor process and worker configuration."""
    
    def __init__(
        self,
        docker_client: DockerClient,
        config: BenchConfig,
        bench_name: str
    ):
        """
        Initialize BenchSupervisor.
        
        Args:
            docker_client: Docker client for operations
            config: Bench configuration
            bench_name: Name of the bench
        """
        self.docker_client = docker_client
        self.config = config
        self.bench_name = bench_name
        self.logger = log.get_logger()
    
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
                status_command = 'supervisorctl -c /opt/user/supervisord.conf status all'
                output = self.docker_client.compose.exec(
                    'frappe',
                    status_command,
                    user='frappe',
                    stream=False
                )
                return True
            except DockerException as e:
                if any('frappe-bench' in s for s in e.output.combined):
                    return True
                time.sleep(interval)
                continue
        return False
    
    def restart_supervisor_service(
        self,
        service: str,
        docker_client_obj: Optional[DockerClient] = None,
        timeout: int = 30,
        interval: int = 1
    ) -> bool:
        """
        Restart a supervisor service.
        
        Args:
            service: Service name to restart
            docker_client_obj: Optional Docker client (uses self.docker_client if None)
            timeout: Timeout in seconds
            interval: Check interval in seconds
            
        Returns:
            True if restarted successfully, False otherwise
            
        Raises:
            BenchOperationException: If supervisor socket not created or restart fails
        """
        socket_path = f"/fm-sockets/{service}.sock"
        
        # Wait for supervisor socket file to be created in container
        for _ in range(timeout):
            try:
                self.docker_client.compose.exec(
                    service=service,
                    user='frappe',
                    command=f"test -e {socket_path}",
                    stream=False
                )
                break
            except DockerException:
                time.sleep(interval)
        else:
            raise BenchOperationException(
                self.bench_name,
                message=f'Supervisor socket for {service} service not created after {timeout} seconds'
            )
        
        restart_supervisor_command = 'supervisorctl -c /opt/user/supervisord.conf restart all'
        
        if not docker_client_obj:
            docker_client_obj = self.docker_client
        
        # Check if service is running
        try:
            all_statuses = docker_client_obj.compose.get_all_services_status()
            service_running = any(
                status["Service"] == service and status["State"] == "running"
                for status in all_statuses
            )
        except DockerException:
            service_running = False
        
        if not service_running:
            richprint.error(text=f'Service [blue]{service}[/blue] not running.')
            return False
        
        # Execute restart command
        try:
            docker_client_obj.compose.exec(
                service=service,
                user='frappe',
                command=restart_supervisor_command,
                stream=False
            )
            return True
        except DockerException as e:
            raise BenchOperationException(
                self.bench_name,
                message=f'Failed to restart supervisor for {service} service'
            )
    
    def switch_bench_env(
        self,
        service: str = 'frappe',
        timeout: int = 30,
        interval: int = 1
    ) -> None:
        """
        Switch bench environment between dev and prod.
        
        Args:
            service: Service name (default: frappe)
            timeout: Timeout in seconds
            interval: Check interval in seconds
            
        Raises:
            BenchOperationException: If supervisor socket not created
        """
        from frappe_manager.site_manager.site_exceptions import BenchFrappeServiceSupervisorNotRunning
        
        if not self.is_supervisord_running():
            raise BenchFrappeServiceSupervisorNotRunning(self.bench_name)
        
        socket_path = f"/fm-sockets/frappe.sock"
        
        # Wait for supervisor socket file to be created in container
        for _ in range(timeout):
            try:
                self.docker_client.compose.exec(
                    service=service,
                    command=f"test -e {socket_path}",
                    user='frappe',
                    stream=False
                )
                break
            except DockerException as e:
                time.sleep(interval)
        else:
            raise BenchOperationException(
                self.bench_name,
                message=f'Supervisor socket for frappe service not created after {timeout} seconds'
            )
        
        supervisorctl_command = f"supervisorctl -s unix:///{socket_path} "
        
        if self.config.environment_type == FMBenchEnvType.dev:
            richprint.change_head(f"Configuring and starting {self.config.environment_type.value} services")
            
            stop_command = supervisorctl_command + "stop all"
            self._run_frappe_command(stop_command)
            
            unlink_command = 'rm -rf /opt/user/conf.d/web.fm.supervisor.conf'
            self._run_frappe_command(unlink_command)
            
            link_command = 'ln -sfn /opt/user/frappe-dev.conf /opt/user/conf.d/frappe-dev.conf'
            self._run_frappe_command(link_command)
            
            reread_command = supervisorctl_command + "reread"
            self._run_frappe_command(reread_command)
            
            update_command = supervisorctl_command + "update"
            self._run_frappe_command(update_command)
            
            start_command = supervisorctl_command + "start all"
            self._run_frappe_command(start_command)
            
            richprint.print(f"Configured and Started {self.config.environment_type.value} services.")
            
        elif self.config.environment_type == FMBenchEnvType.prod:
            richprint.change_head(f"Configuring and starting {self.config.environment_type.value} services")
            
            stop_command = supervisorctl_command + "stop all"
            self._run_frappe_command(stop_command)
            
            unlink_command = 'rm -rf /opt/user/conf.d/frappe-dev.conf'
            self._run_frappe_command(unlink_command)
            
            link_command = (
                'ln -sfn /workspace/frappe-bench/config/web.fm.supervisor.conf /opt/user/conf.d/web.fm.supervisor.conf'
            )
            self._run_frappe_command(link_command)
            
            reread_command = supervisorctl_command + "reread"
            self._run_frappe_command(reread_command)
            
            update_command = supervisorctl_command + "update"
            self._run_frappe_command(update_command)
            
            start_command = supervisorctl_command + "start all"
            self._run_frappe_command(start_command)
            
            richprint.print(f"Configured and Started {self.config.environment_type.value} services.")
    
    def _run_frappe_command(self, command: str) -> None:
        """
        Run a command in the frappe service.
        
        Args:
            command: Command to execute
            
        Raises:
            DockerException: If command fails
        """
        try:
            self.docker_client.compose.exec('frappe', command, user='frappe', stream=False)
        except DockerException as e:
            from frappe_manager.site_manager.site_exceptions import BenchException
            raise BenchException("frappe", f"Failed to run {command} in frappe service.")
