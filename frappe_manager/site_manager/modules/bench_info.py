"""
BenchInfo Module

Handles information retrieval and display for the bench including:
- Displaying comprehensive bench information
- Reading config files (common_site_config.json, site_config.json)
- Getting installed apps list
- Getting log file paths
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.table import Table

from frappe_manager.docker import DockerException
from frappe_manager.output_manager import OutputHandler, temporary_stop
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.exceptions import BenchException
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.helpers import format_ssl_certificate_time_remaining
from frappe_manager.utils.site import generate_services_table

if TYPE_CHECKING:
    from frappe_manager.services_manager.services import ServicesManager
    from frappe_manager.site_manager.bench_config import BenchConfig
    from frappe_manager.site_manager.modules.bench_admin_tools import BenchAdminTools
    from frappe_manager.site_manager.modules.bench_workers import BenchWorkers
    from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager


class BenchInfo:
    """
    Manages information retrieval and display for a bench.

    Responsibilities:
    - Display comprehensive bench information
    - Read configuration files
    - Get installed apps list
    - Get log file paths
    """

    def __init__(
        self,
        bench_name: str,
        bench_path: Path,
        bench_config: "BenchConfig",
        services: "ServicesManager",
        workers: "BenchWorkers",
        admin_tools: "BenchAdminTools",
        certificate_manager: "SSLCertificateManager",
        get_db_connection_info_fn,
        has_certificate_fn,
        is_running_fn,
        get_services_running_status_fn,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchInfo module.

        Args:
            bench_name: Name of the bench
            bench_path: Path to bench directory
            bench_config: Bench configuration object
            services: Services manager instance
            workers: Workers manager instance
            admin_tools: Admin tools instance
            certificate_manager: SSL certificate manager
            get_db_connection_info_fn: Callable to get DB connection info
            has_certificate_fn: Callable to check if certificate exists
            is_running_fn: Callable to check if bench is running
            get_services_running_status_fn: Callable to get services status
            output_handler: Optional output handler for displaying information
        """
        self.bench_name = bench_name
        self.bench_path = bench_path
        self.bench_config = bench_config
        self.services = services
        self.workers = workers
        self.admin_tools = admin_tools
        self.certificate_manager = certificate_manager
        self.get_db_connection_info = get_db_connection_info_fn
        self.has_certificate = has_certificate_fn
        self.is_running = is_running_fn
        self.get_services_running_status = get_services_running_status_fn
        self.output = output_handler or RichOutputHandler()

    def get_common_config(self) -> dict:
        """
        Get common site configuration from common_site_config.json.

        Returns:
            dict: Common site configuration

        Raises:
            BenchException: If common_site_config.json not found
        """
        common_bench_config_path = self.bench_path / "workspace/frappe-bench/sites/common_site_config.json"
        if not common_bench_config_path.exists():
            raise BenchException(self.bench_name, message="common_site_config.json not found.")
        return json.loads(common_bench_config_path.read_text())

    def get_site_config(self) -> dict:
        """
        Get site-specific configuration from site_config.json.

        Returns:
            dict: Site configuration

        Raises:
            BenchException: If site_config.json not found
        """
        site_config_path = self.bench_path / "workspace/frappe-bench/sites" / self.bench_name / "site_config.json"
        if not site_config_path.exists():
            raise BenchException(self.bench_name, message="site_config.json not found.")
        return json.loads(site_config_path.read_text())

    def get_installed_apps_list(self) -> dict[str, Any]:
        """
        Get list of installed apps from apps.json.

        Returns:
            dict: Installed apps data with versions, or empty dict if not found
        """
        apps_json_file = self.bench_path / "workspace/frappe-bench/sites/apps.json"
        if not apps_json_file.exists():
            return {}
        with open(apps_json_file) as f:
            apps_data = json.load(f)
        return apps_data

    def get_python_version(self) -> str:
        """
        Read the active Python version from the uv python-default symlink.

        Returns:
            Version string (e.g. "3.12.9") or "N/A" if not resolvable.
        """
        symlink = self.bench_path / "workspace/frappe-bench/.uv/python-default"
        try:
            target = Path(symlink.readlink()).name  # e.g. cpython-3.12.9-linux-x86_64-gnu
            parts = target.split("-")
            return parts[1] if len(parts) > 1 else "N/A"
        except (OSError, IndexError):
            return "N/A"

    def get_node_version(self) -> str:
        """
        Read the active Node version from the fnm default alias symlink.

        Returns:
            Version string (e.g. "v22.11.0") or "N/A" if not resolvable.
        """
        symlink = self.bench_path / "workspace/frappe-bench/.fnm/aliases/default"
        try:
            target = symlink.readlink()  # e.g. ../node-versions/v22.11.0/installation
            version = next((p for p in target.parts if p.startswith("v")), None)
            return version or "N/A"
        except OSError:
            return "N/A"

    def get_log_file_paths(self) -> list[Path]:
        """
        Get log file paths based on environment type.

        Returns:
            list: List of log file paths
        """
        base_log_dir = self.bench_path / "workspace/frappe-bench/logs"
        if self.bench_config.environment_type.value == "dev":
            bench_dev_server_log_path = base_log_dir / "web.dev.log"
            return [bench_dev_server_log_path]
        bench_prod_server_log_path_stdout = base_log_dir / "web.log"
        bench_prod_server_log_path_stderr = base_log_dir / "web.error.log"
        return [bench_prod_server_log_path_stderr, bench_prod_server_log_path_stdout]

    def display_info(self) -> None:
        """
        Retrieve and display comprehensive information about the bench.

        This includes: URL, root path, status, credentials, database info,
        SSL status, admin tools, installed apps, and running services.
        """
        self.output.change_head("Getting bench info")
        bench_db_info = self.get_db_connection_info()

        db_user = bench_db_info.get("name", "N/A")
        db_pass = bench_db_info.get("password", "N/A")

        services_db_info = self.services.database_manager.database_server_info
        bench_info_table = Table(show_lines=True, show_header=False, highlight=True)

        protocol = "https" if self.has_certificate() else "http"

        # Get admin password from site_config.json if available
        admin_pass = self.bench_config.admin_pass + " (default)"
        site_config = self.get_site_config()
        if "admin_password" in site_config:
            admin_pass = site_config["admin_password"]

        ssl_cert = self.bench_config.get_primary_certificate()
        ssl_service_type = f"{ssl_cert.ssl_type.value}"
        if ssl_cert.ssl_type == SUPPORTED_SSL_TYPES.le:
            if isinstance(ssl_cert, LetsencryptSSLCertificate):
                ssl_service_type = f"[{ssl_cert.challenge_type.value}] {ssl_cert.ssl_type.value}"
            else:
                ssl_service_type = f"{ssl_cert.ssl_type.value}"

        is_running = self.is_running()
        status = "Active" if is_running else "Inactive"
        status_color = "green" if is_running else "red"
        status_display = f"[{status_color}]{status}[/{status_color}]"

        data = {
            "Bench Url": f"{protocol}://{self.bench_name}",
            "Bench Root": f"[link=file://{self.bench_path.absolute()}]{self.bench_path.absolute()}[/link]",
            "Status": status_display,
            "Python Version": self.get_python_version(),
            "Node Version": self.get_node_version(),
            "Frappe Username": "administrator",
            "Frappe Password": admin_pass,
            "Root DB User": services_db_info.user,
            "Root DB Password": services_db_info.password,
            "Root DB Host": services_db_info.host,
            "DB Name": db_user,
            "DB User": db_user,
            "DB Password": db_pass,
            "Environment": self.bench_config.environment_type.value,
            "HTTPS": (
                f"{ssl_service_type.upper()} ({format_ssl_certificate_time_remaining(self.certificate_manager.get_certificate_expiry())})"
                if self.has_certificate()
                else "Not Enabled"
            ),
        }

        data["Runtime"] = self.bench_config.runtime.value
        if self.bench_config.runtime == BenchRuntime.image:
            deploy_state = self.bench_config.deploy_state
            if deploy_state and deploy_state.current_tag:
                data["Deployed Tag"] = deploy_state.current_tag
                if deploy_state.previous_tag:
                    data["Previous Tag"] = deploy_state.previous_tag
                if deploy_state.last_deploy_at:
                    data["Last Deploy At"] = deploy_state.last_deploy_at
            else:
                data["Deployed Tag"] = "N/A (not yet deployed)"

        # Add alias domains if present (independent of SSL status)
        if self.bench_config.alias_domains:
            alias_list = "\n".join(sorted(self.bench_config.alias_domains))
            data["Alias Domains"] = alias_list

        if not self.bench_config.admin_tools:
            data["Admin Tools"] = "Not Enabled"
        else:
            # Create main admin tools table
            admin_tools_Table = Table(show_lines=False, show_edge=False, pad_edge=False, expand=True)
            admin_tools_Table.add_column("Service", style="cyan")
            admin_tools_Table.add_column("URL", style="blue")

            # Get auth credentials
            username = self.bench_config.admin_tools_username or "admin"
            password = self.bench_config.admin_tools_password or "protected"

            # Create auth info section
            auth_info = f"\nAuthentication Required:\n  Username: [cyan]{username}[/cyan]\n  Password: [green]{password}[/green]"

            admin_tools_Table.add_row("Mailpit", f"{protocol}://{self.bench_name}/mailpit")
            admin_tools_Table.add_row("Adminer", f"{protocol}://{self.bench_name}/adminer")

            # Combine table and auth info
            from rich.console import Group

            data["Admin Tools"] = Group(admin_tools_Table, auth_info)

        bench_info_table.add_column(no_wrap=True)
        bench_info_table.add_column(no_wrap=True)

        for key in data:
            bench_info_table.add_row(key, data[key])

        # Get bench apps data
        apps_json = self.get_installed_apps_list()
        if apps_json:
            bench_apps_list_table = Table(show_lines=True, show_edge=False, pad_edge=False, expand=True)
            bench_apps_list_table.add_column("App")
            bench_apps_list_table.add_column("Version")
            for app in apps_json.keys():
                bench_apps_list_table.add_row(app, apps_json[app]["version"])
            bench_info_table.add_row("Bench Apps", bench_apps_list_table)

        running_bench_services = self.get_services_running_status()

        # Get workers status using docker_client
        try:
            services = self.workers.compose_file_manager.get_services_list()
            containers = self.workers.compose_file_manager.get_container_names().values()
            all_statuses = self.workers.docker_client.compose.get_all_services_status()
            running_bench_workers = {
                status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
            }
        except DockerException:
            running_bench_workers = {}

        # Get admin tools services status directly from docker_client
        running_bench_admin_tools = {}
        if self.admin_tools.compose_file_manager.exists():
            try:
                services = self.admin_tools.compose_file_manager.get_services_list()
                containers = self.admin_tools.compose_file_manager.get_container_names().values()
                all_statuses = self.admin_tools.docker_client.compose.get_all_services_status()
                running_bench_admin_tools = {
                    status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
                }
            except Exception:
                running_bench_admin_tools = {}

        if running_bench_services:
            bench_services_table = generate_services_table(running_bench_services)
            bench_info_table.add_row("Bench Services", bench_services_table)

        if running_bench_workers:
            bench_workers_table = generate_services_table(running_bench_workers)
            bench_info_table.add_row("Bench Workers", bench_workers_table)

        if running_bench_admin_tools:
            bench_admin_table = generate_services_table(running_bench_admin_tools)
            bench_info_table.add_row("Bench Admin Tools", bench_admin_table)

        # Stop spinner temporarily to print the table without corruption
        with temporary_stop(self.output):
            console = Console()
            console.print(bench_info_table)
