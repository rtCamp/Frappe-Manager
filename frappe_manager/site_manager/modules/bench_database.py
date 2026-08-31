"""
BenchDatabase Module

Handles database operations for the bench including:
- Database connection information retrieval
- Database and user removal
- Common site config synchronization
"""

from pathlib import Path
from typing import TYPE_CHECKING

from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.utils.helpers import get_container_name_prefix, get_bench_connection_config
from frappe_manager.utils.site import get_bench_db_connection_info

if TYPE_CHECKING:
    from frappe_manager.services_manager.services import ServicesManager
    from frappe_manager.site_manager.bench_config import BenchConfig


class BenchDatabase:
    """
    Manages database operations for a bench.

    Responsibilities:
    - Get database connection information
    - Remove database and user from global-db
    - Sync common site config with the bench's redis wiring
    """

    def __init__(
        self,
        bench_name: str,
        bench_path: Path,
        bench_config: "BenchConfig",
        services: "ServicesManager",
        set_common_bench_config_fn,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchDatabase module.

        Args:
            bench_name: Name of the bench
            bench_path: Path to bench directory
            bench_config: Bench configuration, the source of the bench's redis wiring
            services: Services manager instance
            set_common_bench_config_fn: Callable to set common bench config
            output_handler: Optional output handler for displaying information
        """
        self.bench_name = bench_name
        self.bench_path = bench_path
        self.bench_config = bench_config
        self.services = services
        self.set_common_bench_config = set_common_bench_config_fn
        self.output = output_handler or RichOutputHandler()

    def get_connection_info(self, site: str | None = None) -> dict:
        """
        Get database connection information for one site.

        Args:
            site: which site's wiring to read. None means the bench's own site, which is the only
                one a single-site bench has.

        Returns:
            dict: Database connection info containing name, user, password, host, port
        """
        return get_bench_db_connection_info(site or self.bench_config.primary_site, self.bench_path)

    def remove_database_and_user(self, site: str | None = None):
        """
        Drop one site's schema and its user from global-db.

        Keyed by SITE. It read `sites/<bench name>/site_config.json` before, which stopped being the
        site directory when bench and site names came apart: it found nothing, `name` was absent, and
        this method returned having dropped nothing while the caller reported success. The schema was
        left behind in global-db with the only record of its name inside a directory about to be
        removed.

        Args:
            site: which site's schema to drop. None means the bench's own site.
        """
        bench_db_info = self.get_connection_info(site)
        self.output.change_head("Removing bench db and db users from global-db")

        if "name" in bench_db_info:
            db_name = bench_db_info["name"]
            db_user = bench_db_info["user"]

            # Remove database
            if not self.services.database_manager.check_db_exists(db_name):
                self.output.warning(f"global-db: Bench db [fm.info]{db_name}[/fm.info] not found. Skipping..")
            else:
                self.services.database_manager.remove_db(db_name)
                self.output.print(f"global-db: Removed bench db [fm.info]{db_name}[/fm.info]")

            # Remove user
            if not self.services.database_manager.check_user_exists(db_user):
                self.output.warning(f"global-db: Bench db user [fm.info]{db_user}[/fm.info] not found. Skipping..")
            else:
                self.services.database_manager.remove_user(db_user, remove_all_host=True)
                self.output.print(f"global-db: Removed bench db users [fm.info]{db_user}[/fm.info]")

    def sync_common_site_config(self):
        """
        Sync `common_site_config.json` with this bench's redis wiring.

        Redis only, and config-driven. The database endpoint is per site and lives in
        `sites/<site>/site_config.json`, so a re-sync must neither mint nor overwrite db keys:
        doing so would clobber an external bench back to the container names. An external
        `[redis]` is used verbatim; without one the per-bench redis containers are addressed
        exactly as before.
        """
        container_prefix = get_container_name_prefix(self.bench_name)
        redis = self.bench_config.redis
        common_site_config_data = get_bench_connection_config(
            container_prefix, redis.cache if redis else None, redis.queue if redis else None
        )
        common_site_config_data["socketio_port"] = "80"
        self.set_common_bench_config(common_site_config_data)
