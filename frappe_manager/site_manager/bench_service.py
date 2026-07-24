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

from frappe_manager.docker import ComposeFile, DockerClient
from frappe_manager.logger import log
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.site_manager.site import Bench


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
            output_handler=self.output,
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
        compose_path = bench_path / "docker-compose.yml"

        compose_file_manager = ComposeFile(compose_path)
        docker_client = DockerClient(compose_file_path=compose_path, output=self.output)

        from frappe_manager.logger.context import LoggerContext
        from frappe_manager.logger.contextual import ContextualLogger

        bench_logger = ContextualLogger(self.logger, context=LoggerContext(bench=bench_name, operation="create"))

        bench = Bench(
            logger=bench_logger,
            path=bench_path,
            name=bench_name,
            bench_config=bench_config,
            compose_file_manager=compose_file_manager,
            docker_client=docker_client,
            services=self.services,
            verbose=self.verbose,
            output_handler=self.output,
        )

        bench.create(is_template_bench=is_template)
        return bench

    def delete_bench(
        self,
        bench_name: str,
        yes: bool = False,
        delete_db_from_global_db: bool | None = None,
    ) -> bool:
        try:
            bench = self.get_bench(bench_name, workers_check=False, admin_tools_check=False)
        except FileNotFoundError:
            bench = self._create_cleanup_bench(bench_name)

        if yes:
            from frappe_manager.output_manager import spinner

            with spinner(self.output, "Removing bench"):
                try:
                    bench.remove_certificate()
                except Exception as e:
                    self.output.warning(str(e))

                try:
                    self._handle_database_deletion(bench, delete_db_from_global_db)
                except Exception as e:
                    self.output.warning(f"Database deletion failed: {e!s}")
                    self.output.warning("Continuing with bench removal...")

                bench.remove_containers_and_dirs()
            return True
        return bench.remove_bench(delete_db_from_global_db=delete_db_from_global_db)

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

    def list_benches_data(self) -> list[dict]:
        """Collect per-bench facts for ``fm list`` (single source for table + --json).

        Cheap host-side reads (config, apps.txt) plus one docker liveness check per
        bench. A bench with a broken/missing config is still listed, with ``error``.
        """
        rows: list[dict] = []
        for bench_name in self.discover_benches():
            try:
                bench = self.get_bench(bench_name, workers_check=False, admin_tools_check=False)
                config = bench.bench_config

                apps_txt = bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt"
                if apps_txt.exists():
                    apps = [n.strip() for n in apps_txt.read_text().splitlines() if n.strip()]
                else:
                    apps = [a.name for a in config.apps_list]

                deploy_state = config.deploy_state
                rows.append(
                    {
                        "name": bench.name,
                        "status": "active" if bench.running else "inactive",
                        "runtime": config.runtime.value,
                        "environment": config.environment_type.value,
                        "apps": apps,
                        "deployed_tag": deploy_state.current_tag if deploy_state else None,
                        "previous_tag": deploy_state.previous_tag if deploy_state else None,
                        "base_image": config.base_image,
                        "seed_image": config.seed_image,
                        "alias_domains": list(config.alias_domains or []),
                        "developer_mode": config.developer_mode,
                        "admin_tools": config.admin_tools,
                        "restart_policy": config.restart_policy.value,
                        "path": str(bench.path),
                        "error": None,
                    }
                )
            except FileNotFoundError as e:
                rows.append(
                    {
                        "name": bench_name,
                        "status": "unknown",
                        "error": f"bench config not found at {e.filename}",
                        "path": str(self.benches_directory / bench_name),
                    }
                )
        return rows

    def list_benches_view(self):
        """Card-per-bench view for ``fm list``.

        A table forces every fact to compete for horizontal space (truncated
        tags/paths); record cards give each fact its own wrapping line, so
        adding facts scales vertically. Returns a rich renderable, or None
        when no benches exist.
        """
        self.output.change_head("Generating bench list")

        rows = self.list_benches_data()
        if not rows:
            self.output.stop()
            self.output.print(
                "Seems like you haven't created any sites yet. "
                "To create a bench, use the command: 'fm create <benchname>'.",
                emoji_code=":white_check_mark:",
            )
            return None

        from frappe_manager.output_manager import railcard

        items: list[railcard.Card] = []
        for row in rows:
            if row.get("error"):
                self.output.warning(f"[fm.error][fm.name]{row['name']}[/fm.name][/fm.error] : {row['error']}")
                continue

            active = row["status"] == "active"
            card = railcard.Card(
                row["name"],
                railcard.bench_meta(active, row["runtime"], row["environment"], row["restart_policy"]),
                active,
                link=f"http://{row['name']}",
            )
            card.fact("apps", ", ".join(row["apps"]) or "-")
            if row["deployed_tag"]:
                card.fact("tag", row["deployed_tag"])
            if row["base_image"]:
                card.fact("base", row["base_image"])
            if row["seed_image"]:
                card.fact("seeded", row["seed_image"])
            if row["alias_domains"]:
                card.fact("domains", ", ".join(row["alias_domains"]))
            card.fact("dir", f"[fm.muted]{row['path']}[/fm.muted]")
            items.append(card)

        self.output.stop()
        return railcard.cards(items)

    def _create_cleanup_bench(self, bench_name: str) -> Bench:
        """
        Create a minimal bench instance for cleanup purposes.

        Used when bench config is missing but we need to clean up containers/files.

        Args:
            bench_name: Name of bench to clean up

        Returns:
            Minimal Bench instance
        """
        import os

        bench_path = self.benches_directory / bench_name
        compose_path = bench_path / "docker-compose.yml"

        compose_file_manager = ComposeFile(compose_path)
        docker_client = DockerClient(compose_file_path=compose_path, output=self.output)

        fake_config = BenchConfig(
            name=bench_name,
            userid=os.getuid(),
            usergroup=os.getgid(),
            apps_list=[],
            developer_mode=False,
            admin_tools=False,
            admin_pass="pass",
            environment_type=FMBenchEnvType.dev,
            root_path=bench_path / "bench_config.toml",
            admin_tools_username=None,
            admin_tools_password=None,
            github_token=None,
            use_uv=True,
            python_version=None,
            node_version=None,
            db_name=None,
            migration_state=None,
        )

        from frappe_manager.logger.context import LoggerContext
        from frappe_manager.logger.contextual import ContextualLogger

        cleanup_logger = ContextualLogger(self.logger, context=LoggerContext(bench=bench_name, operation="cleanup"))

        return Bench(
            logger=cleanup_logger,
            path=bench_path,
            name=bench_name,
            bench_config=fake_config,
            compose_file_manager=compose_file_manager,
            docker_client=docker_client,
            services=self.services,
            workers_check=False,
            admin_tools_check=False,
            verbose=self.verbose,
            output_handler=self.output,
        )

    def _is_using_global_db(self, bench: Bench) -> bool:
        """
        Check if bench is using FM's managed global-db service.

        Args:
            bench: Bench instance to check

        Returns:
            True if bench uses global-db, False otherwise
        """
        try:
            db_info = bench.database.get_connection_info()
            db_host = db_info.get("host", "")

            return db_host == "global-db"
        except Exception:
            return False

    def _handle_database_deletion(self, bench: Bench, delete_db_from_global_db: bool | None):
        """
        Handle database deletion based on user preference and database location.

        Args:
            bench: Bench instance
            delete_db_from_global_db: User preference for database deletion.
                                     None = prompt if using global-db
                                     True = delete from global-db
                                     False = don't delete from global-db
        """
        is_global_db = self._is_using_global_db(bench)

        if not is_global_db:
            self.output.print("Bench is not using FM's managed global-db. Skipping database deletion")
            return

        should_delete = delete_db_from_global_db

        if should_delete is None:
            params = {
                "prompt": f"🗄️  Do you want to remove the database '[bold]{bench.name}[/bold]' from global-db?",
                "choices": ["yes", "no"],
                "default": "yes",
                "required_flag": "--delete-db-from-global-db or --no-delete-db-from-global-db",
            }
            choice = self.output.prompt_ask(**params)
            should_delete = choice == "yes"

        if should_delete:
            bench.remove_database_and_user()
        else:
            self.output.print("Skipping database deletion from global-db")
