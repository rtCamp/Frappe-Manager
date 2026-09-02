"""
Bench Admin Tools Module

Handles admin tools (Mailpit, Adminer) management including:
- Docker compose generation and lifecycle
- Adminer login plugin (one-click cards for site DB and redis) placement
- Nginx location configuration
- HTTP authentication setup
- Mailpit integration with Frappe
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from frappe_manager import CLI_DEFAULT_DELIMETER
from frappe_manager.docker import ComposeFile, DockerClient, DockerException
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.exceptions import AdminToolsFailedToStart, AdminToolsFailedToStop, BenchException
from frappe_manager.utils.helpers import get_container_name_prefix, get_current_fm_version, get_template_path

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Bench


class BenchAdminTools:
    def __init__(
        self,
        bench: "Bench",
        nginx_proxy: Any,
        verbose: bool = True,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchAdminTools.

        Args:
            bench: The Bench instance
            nginx_proxy: Nginx proxy manager
            verbose: Whether to show verbose output
            output_handler: Optional output handler for displaying information
        """
        self.bench = bench
        self.compose_path = bench.path / "docker-compose.admin-tools.yml"
        self.bench_name = bench.name
        self.output = output_handler or RichOutputHandler()

        self.compose_file_manager = ComposeFile(self.compose_path, template_name="docker-compose.admin-tools.tmpl")

        self.docker_client = DockerClient(compose_file_path=self.compose_path, output=self.output)

        self.nginx_proxy = nginx_proxy
        self.nginx_config_location_path: Path = self.nginx_proxy.dirs.conf.host / "custom" / "admin-tools.conf"
        self.adminer_config_path: Path = bench.path / "configs" / "adminer"

    def generate_compose(self):
        self.compose_file_manager.yml = self.compose_file_manager.load_template()

        self.compose_file_manager.configure_bench(
            prefix=get_container_name_prefix(self.bench_name),
            version=get_current_fm_version(),
            network_name="site-network",
            auto_save=False,
        )

        self.compose_file_manager.set_all_services_restart(self.bench.bench_config.restart_policy.value)
        self.compose_file_manager.write_to_file()
        self.sync_adminer_plugin()

    def sync_adminer_plugin(self):
        """Place (or refresh) the Adminer login plugin in the bench config dir.

        The plugin is a static asset bind-mounted read-only over the adminer
        container's plugins-enabled directory. It reads site credentials and
        redis hosts live from the mounted sites directory on every request, so
        no bench-specific rendering is required. Always overwritten so fm
        upgrades propagate plugin changes on the next enable/sync.
        """
        self.adminer_config_path.mkdir(parents=True, exist_ok=True)
        plugin_template = get_template_path("adminer/000-fm-login.php")
        (self.adminer_config_path / "000-fm-login.php").write_bytes(plugin_template.read_bytes())

    def create(self):
        self.output.change_head("Generating admin tools configuration")
        self.generate_compose()
        self.output.print("Generating admin tools configuration: Done")

    def _site_location_path(self, site: str) -> Path:
        """One site's tool locations: a drop-in inside that site's own directory."""
        return self.nginx_proxy.dirs.conf.host / "custom" / site / "admin-tools.conf"

    def save_nginx_location_config(self):
        """Render the tool locations into the directory of each site that routes them.

        One file per routed site, not one shared `custom/admin-tools.conf`. That file was included
        in EVERY site's server block, so `/adminer/` could only ever answer on every hostname the
        bench serves. A site whose `admin_tools` is false now gets no file at all, so its block
        carries no `location ^~ /adminer/` and the request falls through to Frappe. That removes
        the route rather than putting a second lock on it, which is the only version of per-site
        tool control that is not a bypass: both hostnames reach the same container.

        The auth block is also per site, because it depends on whether THAT site's web surface is
        gated. The credentials it names are always the bench's: the tools are one pair of
        containers for the whole bench.

        The htpasswd file and the server-level auth conf belong to Bench.ensure_fm_nginx_confs();
        this only renders the per-location directives that follow from the state of both surfaces.
        """
        from jinja2 import Template

        from frappe_manager.site_manager.modules.auth import build_tools_auth_block, container_htpasswd_path

        config = self.bench.bench_config
        auth = config.auth
        template = Template(get_template_path("admin-tools-location.tmpl").read_text())

        for site in config.site_names:
            path = self._site_location_path(site)
            if not config.serves_admin_tools(site):
                path.unlink(missing_ok=True)
                continue
            output = template.render(
                {
                    "mailpit_host": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}mailpit",
                    "adminer_host": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}adminer",
                    "auth_block": build_tools_auth_block(
                        web=config.auth_for(site).web,
                        tools=bool(auth.tools) if auth else True,
                        auth_file=container_htpasswd_path(self.bench_name),
                        allow_ips=auth.allow_ips if auth else [],
                    ),
                }
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output)

        # Every bench that had admin tools before this carries the shared file, which would keep
        # serving `/adminer/` from the hostnames a site just opted out of.
        self.nginx_config_location_path.unlink(missing_ok=True)

    def remove_nginx_location_config(self):
        """Drop the tool locations only, from every site and the shared path.

        The credentials and the htpasswd file are shared with the web surface, so
        disabling admin tools must not destroy them; ensure_fm_nginx_confs()
        removes the htpasswd when no surface is left wanting it.
        """
        for site in self.bench.bench_config.site_names:
            self._site_location_path(site).unlink(missing_ok=True)
        self.nginx_config_location_path.unlink(missing_ok=True)

    def _get_common_site_config_path(self) -> Path:
        return self.compose_path.parent / "workspace/frappe-bench/sites/common_site_config.json"

    def _get_common_site_config(self) -> dict:
        config_path = self._get_common_site_config_path()
        if not config_path.exists():
            raise BenchException(self.bench_name, message="common_site_config.json not found.")
        return json.loads(config_path.read_bytes())

    def _save_common_site_config(self, config: dict):
        self._get_common_site_config_path().write_text(json.dumps(config))

    def configure_mailpit_as_default_server(self):
        self.output.change_head("Configuring Mailpit as default mail server")
        current_common_site_config = self._get_common_site_config()

        new_conf = {
            "mail_port": 1025,
            "mail_server": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}mailpit",
            "disable_mail_smtp_authentication": 1,
        }

        for key, value in new_conf.items():
            if key not in current_common_site_config or not current_common_site_config[key] == value:
                current_common_site_config[key] = value

        self._save_common_site_config(current_common_site_config)
        self.output.print("Configured Mailpit as default mail server")

    def remove_mailpit_as_default_server(self):
        self.output.change_head("Removing Mailpit as default mail server")
        current_common_site_config = self._get_common_site_config()

        new_conf = {
            "mail_port": 1025,
            "mail_server": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}mailpit",
            "disable_mail_smtp_authentication": 1,
        }

        for key, value in new_conf.items():
            if key not in current_common_site_config:
                continue

            if not current_common_site_config[key] == value:
                continue

            del current_common_site_config[key]

        self._save_common_site_config(current_common_site_config)
        self.output.print("Removed Mailpit as default mail server")

    def wait_till_services_started(self, interval=2, timeout=30):
        """
        Wait for admin tools services to start by checking their HTTP ports directly.

        Uses the admin-tools compose file to exec into each service container
        and check its port, avoiding dependency on the bench nginx (which may
        be crash-looping if frappe isn't ready yet).
        """
        admin_tools_services = [
            ("mailpit", "8025"),
            ("adminer", "8080"),
        ]

        for tool_name, tool_port in admin_tools_services:
            running = False
            for _ in range(timeout):
                try:
                    self.docker_client.compose.exec(
                        service=tool_name,
                        command=f"timeout {interval} nc -z localhost {tool_port}",
                        stream=False,
                    )
                    running = True
                    break
                except DockerException:
                    continue

            if not running:
                raise AdminToolsFailedToStart(self.bench_name)

    def enable(self, force_recreate_container: bool = False, force_configure: bool = False):
        """Enable admin tools by starting services."""
        # Ensure the adminer plugin exists before compose up: the bind mount
        # source must be present (docker would create it root-owned otherwise)
        # and this refreshes the plugin after fm upgrades.
        self.sync_adminer_plugin()
        # Use docker_client directly instead of compose_project wrapper
        try:
            self.docker_client.compose.up(
                services=[],
                detach=True,
                pull="never",
                force_recreate=force_recreate_container,
            )
        except DockerException as e:
            # `from e` so docker's own reason survives: without it the user is told only
            # that admin tools failed, and the actual compose error is discarded.
            raise AdminToolsFailedToStart(
                self.bench_name,
                compose_path=self.compose_path,
                services=self.compose_file_manager.get_services_list(),
            ) from e

        self.wait_till_services_started()
        self.save_nginx_location_config()
        self.nginx_proxy.reload()

        if force_configure:
            self.configure_mailpit_as_default_server()

    def stop(self):
        """Stop admin tools containers without removing configuration."""
        try:
            self.docker_client.compose.stop(services=[], timeout=2)
        except DockerException as e:
            raise AdminToolsFailedToStop(
                self.bench_name,
                compose_path=self.compose_path,
                services=self.compose_file_manager.get_services_list(),
            ) from e

    def disable(self):
        """Disable admin tools by stopping services and removing all configuration."""
        self.stop()

        self.remove_nginx_location_config()
        self.nginx_proxy.reload()

        self.remove_mailpit_as_default_server()

        if self.adminer_config_path.exists():
            import shutil

            shutil.rmtree(self.adminer_config_path)

    def is_running(self) -> bool:
        """Check if all admin tools services are running."""
        try:
            services = self.compose_file_manager.get_services_list()
            containers = self.compose_file_manager.get_container_names().values()
            all_statuses = self.docker_client.compose.get_all_services_status()

            running_statuses = {
                status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
            }

            if not services:
                return False

            return all(running_statuses.get(service) == "running" for service in services)
        except Exception:
            return False
