"""
BenchService - Service layer for bench operations

This module provides a clean service layer between the CLI and domain models.
It encapsulates bench creation, retrieval, and listing logic to reduce coupling
between the CLI commands and internal implementation details.

Benefits:
- Single responsibility: manages bench lifecycle
- Dependency injection: receives services, not globals
- Testability: easy to mock in tests
- Reusability: can be used by CLI, API, or other interfaces
"""

from pathlib import Path
from typing import List, Optional, Tuple
from rich.table import Table

from frappe_manager.docker import ComposeFile, DockerClient
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.site import Bench
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.logger import log


class BenchService:
    """
    Service layer for bench operations.
    
    Provides high-level operations for managing benches without exposing
    internal implementation details to the CLI layer.
    
    Attributes:
        benches_directory: Root directory containing all benches
        services: Global services manager instance
        verbose: Whether to enable verbose output
    
    Example:
        >>> service = BenchService(CLI_BENCHES_DIRECTORY, services_manager)
        >>> bench = service.get_bench("mysite.localhost")
        >>> benches = service.list_benches()
    """
    
    def __init__(
        self,
        benches_directory: Path,
        services: ServicesManager,
        verbose: bool = False,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize bench service.
        
        Args:
            benches_directory: Path to directory containing benches
            services: Global services manager
            verbose: Enable verbose output
            output_handler: Handler for output operations
        """
        self.benches_directory = benches_directory
        self.services = services
        self.verbose = verbose
        self.output = output_handler or RichOutputHandler()
        self.logger = log.get_logger()
    
    def get_bench(
        self,
        bench_name: str,
        workers_check: bool = True,
        admin_tools_check: bool = True,
    ) -> Bench:
        """
        Get a bench instance by name.
        
        This is a convenience method that wraps Bench.get_object() with
        the service's configuration.
        
        Args:
            bench_name: Name of the bench to retrieve
            workers_check: Whether to check worker status
            admin_tools_check: Whether to check admin tools status
        
        Returns:
            Bench instance
        
        Raises:
            FileNotFoundError: If bench config not found
            
        Example:
            >>> bench = service.get_bench("mysite.localhost")
            >>> bench.start()
        """
        return Bench.get_object(
            bench_name=bench_name,
            services=self.services,
            workers_check=workers_check,
            admin_tools_check=admin_tools_check,
            verbose=self.verbose,
        )
    
    def create_bench(
        self,
        bench_name: str,
        bench_config: BenchConfig,
        is_template: bool = False,
    ) -> Bench:
        """
        Create a new bench.
        
        Handles all the setup for creating a new bench including:
        - Creating directory structure
        - Initializing docker compose files
        - Creating bench instance
        - Running bench creation process
        
        Args:
            bench_name: Name for the new bench
            bench_config: Configuration for the bench
            is_template: Whether to create a template bench
        
        Returns:
            Created Bench instance
        
        Example:
            >>> config = BenchConfig(name="site.localhost", ...)
            >>> bench = service.create_bench("site.localhost", config)
        """
        bench_path = self.benches_directory / bench_name
        compose_path = bench_path / 'docker-compose.yml'
        
        compose_file_manager = ComposeFile(compose_path)
        docker_client = DockerClient(compose_file_path=compose_path)
        
        bench = Bench(
            path=bench_path,
            name=bench_name,
            bench_config=bench_config,
            compose_file_manager=compose_file_manager,
            docker_client=docker_client,
            services=self.services,
            verbose=self.verbose,
        )
        
        bench.create(is_template_bench=is_template)
        return bench
    
    def delete_bench(
        self,
        bench_name: str,
        force: bool = False,
    ) -> bool:
        """
        Delete a bench.
        
        Args:
            bench_name: Name of bench to delete
            force: Skip confirmation prompt
        
        Returns:
            True if bench was deleted, False if user cancelled
        
        Example:
            >>> deleted = service.delete_bench("old.localhost", force=True)
        """
        try:
            bench = self.get_bench(
                bench_name,
                workers_check=False,
                admin_tools_check=False
            )
        except FileNotFoundError:
            # Bench config not found, try to create a minimal bench for cleanup
            bench = self._create_cleanup_bench(bench_name)
        
        # If force is True, skip confirmation prompt
        if force:
            self.output.start("Removing bench")
            try:
                bench.remove_certificate()
            except Exception as e:
                self.output.warning(str(e))
            
            bench.remove_database_and_user()
            bench.remove_containers_and_dirs()
            return True
        else:
            # Use the standard remove_bench with prompt
            return bench.remove_bench()
    
    def discover_benches(self) -> dict[str, Path]:
        """
        Discover all benches in the benches directory.
        
        Returns:
            Dictionary mapping bench names to their docker-compose.yml paths
        
        Example:
            >>> benches = service.discover_benches()
            >>> print(f"Found {len(benches)} benches")
        """
        benches = {}
        
        if not self.benches_directory.exists():
            return benches
        
        for bench_dir in self.benches_directory.iterdir():
            if not bench_dir.is_dir():
                continue
            
            bench_name = bench_dir.name
            compose_file = bench_dir / "docker-compose.yml"
            
            if compose_file.exists():
                benches[bench_name] = compose_file
        
        return benches
    
    def get_bench_names(self) -> list[str]:
        """
        Get list of all bench names.
        
        Returns:
            List of bench names
        
        Example:
            >>> names = service.get_bench_names()
            >>> for name in names:
            ...     bench = service.get_bench(name)
        """
        return list(self.discover_benches().keys())
    
    def list_benches_table(self) -> Table:
        """
        Generate a formatted table of all benches.
        
        Returns:
            Rich Table object with bench information
        
        Example:
            >>> table = service.list_benches_table()
            >>> self.output.print(table)
        """
        self.output.change_head("Generating bench list")
        
        bench_dict = self.discover_benches()
        
        if not bench_dict:
            self.output.stop()
            self.output.print(
                "Seems like you haven't created any sites yet. "
                "To create a bench, use the command: 'fm create <benchname>'.",
                emoji_code=":white_check_mark:",
            )
            # Return empty table since there are no benches
            table = Table(show_lines=True, show_header=True, highlight=True)
            table.add_column("Site")
            table.add_column("Status", vertical="middle")
            table.add_column("Path")
            return table
        
        table = Table(show_lines=True, show_header=True, highlight=True)
        table.add_column("Site")
        table.add_column("Status", vertical="middle")
        table.add_column("Path")
        
        for bench_name in bench_dict.keys():
            try:
                bench = self.get_bench(
                    bench_name,
                    workers_check=False,
                    admin_tools_check=False
                )
                
                row_data = f"[link=http://{bench.name}]{bench.name}[/link]"
                path_data = f"[link=file://{bench.path}]{bench.path}[/link]"
                
                status_color = "white"
                status_msg = "Inactive"
                
                if bench.running:
                    status_color = "green"
                    status_msg = "Active"
                
                status_data = f"[{status_color}]{status_msg}[/{status_color}]"
                
                table.add_row(row_data, status_data, path_data, style=f"{status_color}")
                self.output.update_live(table, padding=(0, 0, 0, 0))
                
            except FileNotFoundError as e:
                self.output.warning(
                    f'[red][bold]{bench_name}[/bold][/red] : '
                    f'Bench config not found at {e.filename}'
                )
        
        self.output.stop()
        return table
    
    def _create_cleanup_bench(self, bench_name: str) -> Bench:
        """
        Create a minimal bench instance for cleanup purposes.
        
        Used when bench config is missing but we need to clean up containers/files.
        
        Args:
            bench_name: Name of bench to clean up
        
        Returns:
            Minimal Bench instance
        """
        from frappe_manager import STABLE_APP_BRANCH_MAPPING_LIST
        from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
        from frappe_manager.ssl_manager.certificate import SSLCertificate
        import os
        
        bench_path = self.benches_directory / bench_name
        compose_path = bench_path / 'docker-compose.yml'
        
        compose_file_manager = ComposeFile(compose_path)
        docker_client = DockerClient(compose_file_path=compose_path)
        
        # Create minimal config for cleanup
        fake_config = BenchConfig(
            name=bench_name,
            userid=os.getuid(),
            usergroup=os.getgid(),
            apps_list=[],
            frappe_branch=STABLE_APP_BRANCH_MAPPING_LIST['frappe'],
            developer_mode=False,
            admin_tools=False,
            admin_pass='pass',
            environment_type=FMBenchEnvType.dev,
            ssl=SSLCertificate(domain=bench_name, ssl_type=SUPPORTED_SSL_TYPES.none),
            root_path=bench_path / "bench_config.toml",
            admin_tools_username=None,
            admin_tools_password=None,
        )
        
        return Bench(
            path=bench_path,
            name=bench_name,
            bench_config=fake_config,
            compose_file_manager=compose_file_manager,
            docker_client=docker_client,
            services=self.services,
            workers_check=False,
            admin_tools_check=False,
            verbose=self.verbose,
        )
