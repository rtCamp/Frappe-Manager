"""
BenchOrchestrator - Complex workflow orchestration for bench operations

This module handles multi-step orchestration workflows that require coordination
between multiple modules and services. It extracts complex business logic from
the main Bench class to keep it as a thin facade.

The orchestrator encapsulates:
- Bench creation workflow
- Bench startup workflow
- Alias domain updates workflow
- Other complex multi-step operations

By centralizing orchestration logic here, we maintain separation of concerns:
- Individual modules handle specific responsibilities
- Orchestrator coordinates between modules
- Bench class remains a simple interface
"""

import copy
from typing import TYPE_CHECKING

from frappe_manager import STABLE_APP_BRANCH_MAPPING_LIST
from frappe_manager.logger import log
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import FMBenchEnvType
from frappe_manager.site_manager.exceptions import BenchOperationException

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Bench


class BenchOrchestrator:
    """
    Orchestrator for complex multi-step bench workflows.

    This class coordinates between multiple modules to execute complex
    workflows that require specific sequencing and error handling.

    Attributes:
        bench: Reference to parent Bench instance
        logger: Logger instance for this orchestrator

    Example:
        >>> orchestrator = BenchOrchestrator(bench_instance)
        >>> orchestrator.create_bench(is_template=False)
    """

    def __init__(self, bench: "Bench", output_handler: OutputHandler | None = None):
        """
        Initialize orchestrator with bench reference.

        Args:
            bench: Parent Bench instance that owns this orchestrator
            output_handler: Output handler for UI/logging (defaults to RichOutputHandler)
        """
        self.bench = bench
        self.logger = log.get_logger()
        self.output = output_handler or RichOutputHandler()

    def create_bench(self, is_template_bench: bool = False):
        """
        Orchestrate the complete bench creation workflow.

        This method coordinates the creation of a new bench by:
        1. Checking Docker images availability
        2. Creating directory structure
        3. Generating docker-compose files
        4. Starting services
        5. Configuring Frappe environment
        6. Installing apps
        7. Creating bench site
        8. Verifying bench is operational

        Args:
            is_template_bench: If True, creates a minimal bench without site creation

        Raises:
            Exception: If any step in the creation process fails
        """
        bench = self.bench

        bench.docker_ops.check_required_docker_images_available()

        try:
            self.output.change_head("Creating Bench Directory")
            bench.path.mkdir(parents=True, exist_ok=True)

            self.output.change_head("Generating bench compose")
            bench.generate_compose(bench.bench_config.export_to_compose_inputs())
            bench.create_compose_dirs()

            if is_template_bench:
                self._create_template_bench()
                return

            self.output.change_head("Starting bench services")
            output = bench.docker_client.compose.up(
                services=[],
                detach=True,
                pull="never",
                force_recreate=True,
                stream=bench.quiet,
            )
            if bench.quiet:
                self.output.live_lines(output, padding=(0, 0, 0, 2))
            self.output.print("Started bench services.")

            self.output.change_head("Creating bench and bench site.")

            # Configure common_site_config.json
            self.output.change_head("Configuring common_site_config.json")
            common_site_config_data = bench.bench_config.get_commmon_site_config_data(
                bench.services.database_manager.database_server_info,
            )
            bench.set_common_bench_config(common_site_config_data)
            self.output.print("Configured common_site_config.json")

            # Configure frappe server
            self.output.change_head("Configuring frappe server")
            bench.site_manager.setup_frappe_server_config()
            self.output.print("Configured frappe server")

            # Setup supervisor
            bench.supervisor.setup_supervisor(bench.path, force=True)

            # Change frappe branch if needed
            # Extract frappe branch from apps_list (frappe is always first)
            frappe_branch = STABLE_APP_BRANCH_MAPPING_LIST.get("frappe")  # Default
            if bench.bench_config.apps_list:
                frappe_app = bench.bench_config.apps_list[0]  # Frappe is always first
                if frappe_app.get("app") in ["frappe", "frappe/frappe"]:
                    frappe_branch = frappe_app.get("branch") or frappe_branch

            bench.app_manager.change_app_branch(
                app="frappe",
                branch=frappe_branch,
                prebaked_branch=STABLE_APP_BRANCH_MAPPING_LIST.get("frappe"),
            )

            # Wait for required services
            bench.site_manager.wait_for_required_services()

            # Install apps to environment (NEW: With github_token and use_uv support)
            bench.app_manager.install_apps(
                bench.bench_config.apps_list,
                github_token=bench.bench_config.github_token,
                use_uv=bench.bench_config.use_uv,
            )

            # Remove archived directory
            self._remove_archived_directory()

            # Create bench site
            self.output.change_head(f"Creating bench site {bench.name}")
            bench.site_manager.create_bench_site()
            self.output.print(f"Created bench site {bench.name}")

            # Install apps to site
            bench.app_manager.install_apps_to_site()

            # Set admin password in site config
            bench.set_bench_site_config({"admin_password": bench.bench_config.admin_pass})

            bench.sync_bench_config_configuration()
            bench.switch_bench_env()

            self.output.change_head("Configuring bench workers.")
            bench.sync_workers_compose(force_recreate=True, setup_supervisor=False)
            self.output.change_head("Configuring bench workers.")
            self.output.update_live()

            bench.save_bench_config()

            self.output.change_head("Commencing site status check")

            # check if bench is created
            if not bench.is_bench_created():
                raise Exception("Bench site is inactive or unresponsive.")

            self.output.print("Bench site is active and responding.")
            self.logger.info(f"{bench.name}: Bench site is active and responding.")

            bench.info()

            if ".localhost" not in bench.name:
                self.output.print(
                    "Please note that You will have to add a host entry to your system's hosts file to access the bench locally.",
                )

        except Exception as e:
            self._handle_creation_failure(e)

    def _create_template_bench(self):
        """Create a template bench (minimal configuration without full site setup)."""
        bench = self.bench
        global_db_info = bench.services.database_manager.database_server_info
        bench.sync_bench_common_site_config(global_db_info.host, global_db_info.port)
        bench.save_bench_config()
        self.output.print(f"Created template bench: {bench.name}", emoji_code=":white_check_mark:")

    def _remove_archived_directory(self):
        """Remove the archived directory from frappe-bench workspace."""
        bench = self.bench
        command = "rm -rf /workspace/frappe-bench/archived"
        command = f"/bin/bash -c 'source /etc/bash.bashrc; {command}'"
        try:
            bench.docker_client.compose.exec(
                service="frappe",
                command=command,
                user="frappe",
                workdir="/workspace/frappe-bench",
                stream=False,
            )
        except Exception:
            raise BenchOperationException(bench.name, "Failed to remove /workspace/frappe-bench/archived directory.")

    def _handle_creation_failure(self, exception: Exception):
        """Handle failures during bench creation with cleanup."""
        from frappe_manager import CLI_DIR
        from frappe_manager.utils.helpers import capture_and_format_exception

        bench = self.bench

        self.output.stop()
        self.output.display_error(f"[red][bold]Error Occured: [/bold][/red]{exception}")

        exception_traceback_str = capture_and_format_exception()
        self.logger.error(f"{bench.name}: NOT WORKING\n Exception: {exception_traceback_str}")

        log_path = CLI_DIR / "logs" / "fm.log"
        error_message = [
            "There has been some error creating/starting the bench.",
            f":mag: Please check the logs at {log_path}",
        ]
        self.output.display_error("\n".join(error_message))

        if bench.exists:
            remove_status = bench.remove_bench(default_choice=False)
            if not remove_status:
                bench.info()

    def start_bench(
        self,
        force: bool = False,
        sync_bench_config_changes: bool = False,
        reconfigure_workers: bool = False,
        include_default_workers: bool = False,
        include_custom_workers: bool = False,
        reconfigure_supervisor: bool = False,
        reconfigure_common_site_config: bool = False,
        sync_dev_packages: bool = False,
    ):
        """
        Orchestrate the bench startup workflow.

        This method coordinates starting a bench with various configuration options:
        - Starting Docker containers
        - Reconfiguring services if requested
        - Starting admin tools
        - Starting workers
        - Syncing configuration changes

        Args:
            force: Force recreate containers
            sync_bench_config_changes: Sync configuration changes after start
            reconfigure_workers: Regenerate worker configuration
            include_default_workers: Include default workers in reconfiguration
            include_custom_workers: Include custom workers in reconfiguration
            reconfigure_supervisor: Regenerate supervisord configuration
            reconfigure_common_site_config: Reconfigure common_site_config.json
            sync_dev_packages: Install/remove dev packages based on environment
        """
        bench = self.bench

        bench.docker_ops.check_required_docker_images_available()

        # Reconfigure common_site_config.json if required
        if reconfigure_common_site_config:
            self.output.print("Reconfiguring common_site_config with defaults")
            global_db_info = bench.services.database_manager.database_server_info
            bench.sync_bench_common_site_config(global_db_info.host, global_db_info.port)

        self.output.change_head("Starting bench services")
        bench.docker_ops.start(services=[], force_recreate=force, pull="never")

        # Start admin-tools if exists
        if bench.admin_tools.compose_file_manager.compose_path.exists():
            self.output.change_head("Starting admin tools services")
            bench.admin_tools.enable(force_recreate_container=force)
            self.output.print("Started admin tools services.")

            # Check if nginx service is stopped and restart if needed
            if not bench._is_service_running("nginx"):
                bench.docker_ops.start(services=["nginx"], force_recreate=False, pull="never")

        bench.site_manager.wait_for_required_services()

        # Reconfigure supervisord if requested
        if reconfigure_supervisor:
            self.output.print("Reconfiguring supervisord")
            bench.supervisor.setup_supervisor(bench.path, force=True)

        # Reconfigure workers if requested
        if reconfigure_workers:
            self.output.print("Reconfiguring workers")
            bench.sync_workers_compose(
                include_default_workers=include_default_workers,
                include_custom_workers=include_custom_workers,
            )

        # Sync dev packages if requested
        if sync_dev_packages:
            self.output.print("Syncing dev packages")
            if bench.bench_config.environment_type == FMBenchEnvType.dev:
                bench.install_dev_packages()
            else:
                bench.remove_dev_packages()

        bench.switch_bench_env()

        # Sync bench config changes if requested
        if sync_bench_config_changes:
            self.output.print("Syncing bench configuration changes")
            bench.sync_bench_config_configuration()

        # Start workers if exists
        if bench.workers.compose_file_manager.exists():
            self.output.change_head("Starting bench workers services")
            output = bench.workers.docker_client.compose.up(
                services=[],
                detach=True,
                pull="never",
                force_recreate=force,
                stream=bench.quiet,
            )
            if bench.quiet:
                self.output.live_lines(output, padding=(0, 0, 0, 2))
            self.output.print("Started bench workers services.")

        bench.save_bench_config()
        self.output.print("Started bench services.")

    def update_alias_domains(self, add_domains: list[str] | None = None, remove_domains: list[str] | None = None):
        """
        Update alias domains for the bench.
        - Updates alias configuration
        - Restarts services with new configuration

        SSL certificates are NOT automatically generated for new alias domains.
        Users must explicitly add SSL certificates using: fm ssl add <bench> <domain>
        """
        bench = self.bench

        # Backup current alias domains for rollback
        backup_aliases = copy.deepcopy(bench.bench_config.alias_domains or [])
        current_aliases = set(backup_aliases)

        # Validate and prepare updates
        add_list = add_domains if add_domains else []
        remove_list = remove_domains if remove_domains else []

        # Validation: Check for primary domain in operations
        if bench.name in add_list:
            self.output.warning(f"Skipping '{bench.name}' - primary domain cannot be added as alias.")
            add_list = [d for d in add_list if d != bench.name]

        if bench.name in remove_list:
            self.output.stop()
            raise ValueError(f"Cannot remove primary domain '{bench.name}'. Only alias domains can be removed.")

        # Add domains
        added_domains = []
        for domain in add_list:
            if domain in current_aliases:
                self.output.warning(f"Domain '{domain}' is already an alias. Skipping.")
            else:
                current_aliases.add(domain)
                added_domains.append(domain)

        # Check for wildcard domains and warn about DNS-01 requirement
        for domain in added_domains:
            if domain.startswith("*."):
                self.output.warning(f"Wildcard domain '{domain}' requires DNS-01 challenge and Cloudflare credentials.")

        # Remove domains
        removed_domains = []
        for domain in remove_list:
            if domain not in current_aliases:
                self.output.warning(f"Domain '{domain}' is not an alias. Skipping.")
            else:
                current_aliases.remove(domain)
                removed_domains.append(domain)

        # Check if any changes were made
        if not added_domains and not removed_domains:
            self.output.print("No changes to apply.")
            return

        # Display changes
        if added_domains:
            self.output.print(f"Adding aliases: {', '.join(added_domains)}")
        if removed_domains:
            self.output.print(f"Removing aliases: {', '.join(removed_domains)}")

        # Update alias list
        updated_aliases = sorted(list(current_aliases))
        bench.bench_config.alias_domains = updated_aliases

        try:
            # Save config and restart services
            self.output.change_head("Saving configuration")
            bench.save_bench_config()
            self.output.print("Configuration saved.")

            self._restart_services_with_updated_config()

            # Inform user about SSL certificate management
            if added_domains:
                self.output.print("")
                self.output.print("To add SSL certificates for new alias domains, use:", emoji_code="")
                for domain in added_domains:
                    self.output.print(f"  fm ssl add {bench.name} {domain}", emoji_code="")

        except Exception as e:
            # Rollback on failure
            bench.bench_config.alias_domains = backup_aliases
            self.output.stop()
            self.logger.error(f"Failed to update alias domains: {e}")
            raise Exception(f"Failed to update alias domains: {e}")

    def _restart_services_with_updated_config(self):
        """Restart all bench services with updated configuration."""
        bench = self.bench

        self.output.change_head("Updating services")
        output = bench.docker_client.compose.stop(services=[], timeout=10, stream=bench.quiet)
        if bench.quiet:
            self.output.live_lines(output, padding=(0, 0, 0, 2))

        # Delete nginx config to force regeneration with new domains
        nginx_config_path = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        if nginx_config_path.exists():
            nginx_config_path.unlink()

        bench.generate_compose(bench.bench_config.export_to_compose_inputs())
        output = bench.docker_client.compose.up(
            services=[],
            detach=True,
            pull="never",
            force_recreate=True,
            stream=bench.quiet,
        )
        if bench.quiet:
            self.output.live_lines(output, padding=(0, 0, 0, 2))

        # Start admin tools if they exist
        if bench.admin_tools.compose_file_manager.compose_path.exists():
            bench.admin_tools.enable(force_recreate_container=True)

        # Ensure required services are available
        bench.site_manager.wait_for_required_services()

        # Start Frappe supervisor processes (critical for app to be accessible)
        bench.switch_bench_env()

        # Start workers if they exist
        if bench.workers.compose_file_manager.exists():
            output = bench.workers.docker_client.compose.up(
                services=[],
                detach=True,
                pull="never",
                force_recreate=True,
                stream=bench.quiet,
            )
            if bench.quiet:
                self.output.live_lines(output, padding=(0, 0, 0, 2))

        self.output.print("Services restarted with updated configuration.")
