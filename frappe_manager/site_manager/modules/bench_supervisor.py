"""
BenchSupervisor - Supervisor Process Management Module

This module handles Supervisor process management for bench services.
Extracted from the monolithic Bench class for better separation of concerns.
"""

import time
from typing import Optional

from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.docker import DockerClient, DockerException
from frappe_manager.logger import log
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.site_manager.exceptions import BenchOperationException


class BenchSupervisor:
    """Manages Supervisor process and worker configuration."""
    
    def __init__(
        self,
        docker_client: DockerClient,
        config: BenchConfig,
        bench_name: str,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchSupervisor.
        
        Args:
            docker_client: Docker client for operations
            config: Bench configuration
            bench_name: Name of the bench
            output_handler: Optional output handler for displaying information
        """
        self.docker_client = docker_client
        self.config = config
        self.bench_name = bench_name
        self.logger = log.get_logger()
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
            self.output.display_error(text=f'Service [blue]{service}[/blue] not running.')
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
        from frappe_manager.site_manager.exceptions import BenchFrappeServiceSupervisorNotRunning
        
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
            self.output.change_head(f"Configuring and starting {self.config.environment_type.value} services")
            
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
            
            self.output.print(f"Configured and Started {self.config.environment_type.value} services.")
            
        elif self.config.environment_type == FMBenchEnvType.prod:
            self.output.change_head(f"Configuring and starting {self.config.environment_type.value} services")
            
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
            
            self.output.print(f"Configured and Started {self.config.environment_type.value} services.")
    
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
            from frappe_manager.site_manager.exceptions import BenchException
            raise BenchException("frappe", f"Failed to run {command} in frappe service.")
    
    def setup_supervisor(self, bench_path, force: bool = False) -> None:
        """
        Set up supervisor configuration for the bench.
        
        Generates supervisor.conf and splits it into individual service configs.
        
        Args:
            bench_path: Path to the bench directory
            force: Force regeneration even if config exists
            
        Raises:
            BenchOperationException: If supervisor setup fails
            
        Example:
            >>> supervisor.setup_supervisor(Path("/path/to/bench"), force=True)
        """
        from pathlib import Path
        
        frappe_bench_dir = bench_path / "workspace" / "frappe-bench"
        config_dir_path: Path = frappe_bench_dir / "config"
        supervisor_conf_path: Path = config_dir_path / "supervisor.conf"
        bench_cli_cmd = ['/opt/user/.bin/bench_orig']
        
        self.output.change_head("Checking supervisor configuration")
        if not supervisor_conf_path.exists() or force:
            self.output.change_head("Configuring supervisor configs")
            
            bench_setup_supervisor_command = bench_cli_cmd + [
                "setup supervisor --skip-redis --skip-supervisord --yes --user frappe"
            ]
            
            bench_setup_supervisor_command = " ".join(bench_setup_supervisor_command)
            bench_setup_supervisor_exception = BenchOperationException(
                self.bench_name, "Failed to configure supervisor."
            )
            
            # Execute command
            command = f"/bin/bash -c 'source /etc/bash.bashrc; {bench_setup_supervisor_command}'"
            try:
                output = self.docker_client.compose.exec(
                    service='frappe',
                    command=command,
                    user='frappe',
                    workdir="/workspace/frappe-bench",
                    stream=False
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
                            value = re.sub(r'-w\s+\d+', f'-w {workers}', value)
                    
                    section_config.set(section_name, key, value)
                
                section_name_delimeter = '-frappe-'
                
                if '-node-' in section_name:
                    section_name_delimeter = '-node-'
                
                file_name_prefix = section_name.split(section_name_delimeter)[-1]
                file_name = file_name_prefix + ".fm.supervisor.conf"
                if "worker" in section_name:
                    file_name = file_name_prefix + ".workers.fm.supervisor.conf"
                
                new_file: Path = supervisor_conf_path.parent / file_name
                with open(new_file, "w") as section_file:
                    section_config.write(section_file)
                
                self.logger.info(f"Split supervisor conf {section_name} => {file_name}")
