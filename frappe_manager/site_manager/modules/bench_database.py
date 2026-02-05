"""
BenchDatabase Module

Handles database operations for the bench including:
- Database connection information retrieval
- Database and user removal
- Common site config synchronization
"""

from pathlib import Path
from typing import TYPE_CHECKING

from frappe_manager import CLI_DEFAULT_DELIMETER
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.utils.helpers import get_container_name_prefix
from frappe_manager.utils.site import get_bench_db_connection_info

if TYPE_CHECKING:
    from frappe_manager.services_manager.services import ServicesManager


class BenchDatabase:
    """
    Manages database operations for a bench.
    
    Responsibilities:
    - Get database connection information
    - Remove database and user from global-db
    - Sync common site config with database settings
    """

    def __init__(
        self,
        bench_name: str,
        bench_path: Path,
        services: "ServicesManager",
        set_common_bench_config_fn,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchDatabase module.
        
        Args:
            bench_name: Name of the bench
            bench_path: Path to bench directory
            services: Services manager instance
            set_common_bench_config_fn: Callable to set common bench config
            output_handler: Optional output handler for displaying information
        """
        self.bench_name = bench_name
        self.bench_path = bench_path
        self.services = services
        self.set_common_bench_config = set_common_bench_config_fn
        self.output = output_handler or RichOutputHandler()

    def get_connection_info(self) -> dict:
        """
        Get database connection information for the bench.
        
        Returns:
            dict: Database connection info containing name, user, password, host, port
        """
        return get_bench_db_connection_info(self.bench_name, self.bench_path)

    def remove_database_and_user(self):
        """
        Remove database and user from global-db for this bench.
        
        This function removes both the database and user associated with the bench
        from the global database service.
        """
        bench_db_info = self.get_connection_info()
        self.output.change_head("Removing bench db and db users from global-db")

        if "name" in bench_db_info:
            db_name = bench_db_info["name"]
            db_user = bench_db_info["user"]

            # Remove database
            if not self.services.database_manager.check_db_exists(db_name):
                self.output.warning(f"global-db: Bench db [blue]{db_name}[/blue] not found. Skipping..")
            else:
                self.services.database_manager.remove_db(db_name)
                self.output.print(f"global-db: Removed bench db [blue]{db_name}[/blue]")

            # Remove user
            if not self.services.database_manager.check_user_exists(db_user):
                self.output.warning(f"global-db: Bench db user [blue]{db_user}[/blue] not found. Skipping..")
            else:
                self.services.database_manager.remove_user(db_user, remove_all_host=True)
                self.output.print(f"global-db: Removed bench db users [blue]{db_user}[/blue]")

    def sync_common_site_config(self, services_db_host: str, services_db_port: int):
        """
        Sync the common site configuration with the global database information.
        
        This function sets the common site configuration data including the socketio port,
        database host and port, and the Redis cache, queue, and socketio URLs.
        
        Args:
            services_db_host: Database host from services
            services_db_port: Database port from services
        """
        container_prefix = get_container_name_prefix(self.bench_name)

        # Set common site config with database and Redis connection info
        common_site_config_data = {
            "socketio_port": "80",
            "db_host": services_db_host,
            "db_port": services_db_port,
            "redis_cache": f"redis://{container_prefix}{CLI_DEFAULT_DELIMETER}redis-cache:6379",
            "redis_queue": f"redis://{container_prefix}{CLI_DEFAULT_DELIMETER}redis-queue:6379",
            "redis_socketio": f"redis://{container_prefix}{CLI_DEFAULT_DELIMETER}redis-cache:6379",
        }
        self.set_common_bench_config(common_site_config_data)
