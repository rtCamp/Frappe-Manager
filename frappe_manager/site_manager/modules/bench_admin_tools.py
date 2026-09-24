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
from frappe_manager.site_manager.exceptions import (
    AdminToolsFailedToStart,
    AdminToolsFailedToStop,
    AdminToolsProbeUnavailable,
    BenchException,
)
from frappe_manager.utils.helpers import get_container_name_prefix, get_current_fm_version, get_template_path
from frappe_manager.utils.site import host_bench_dir

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
        with self.compose_file_manager:
            self.compose_file_manager.yml = self.compose_file_manager.load_template()

            self.compose_file_manager.configure_bench(
                prefix=get_container_name_prefix(self.bench_name),
                version=get_current_fm_version(),
                network_name="site-network",
                auto_save=False,
            )

            self.compose_file_manager.set_all_services_restart(self.bench.bench_config.restart_policy.value)
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

        def _render(site: str | None) -> str:
            return template.render(
                {
                    "mailpit_host": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}mailpit",
                    "adminer_host": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}adminer",
                    "auth_block": build_tools_auth_block(
                        web=config.auth_for(site).web if site else bool(auth and auth.web),
                        tools=bool(auth.tools) if auth else True,
                        auth_file=container_htpasswd_path(self.bench_name),
                        allow_ips=auth.allow_ips if auth else [],
                    ),
                }
            )

        if not self.bench.nginx_conf_serves_per_site():
            # The conf on disk includes only `custom/*.conf`, so per-site files would be written and
            # never read: the tools would answer on no hostname at all while the config said
            # otherwise. One shared file is what every version of the template includes.
            for site in config.site_names:
                self._site_location_path(site).unlink(missing_ok=True)
            self.nginx_config_location_path.parent.mkdir(parents=True, exist_ok=True)
            self.nginx_config_location_path.write_text(_render(None))
            return

        for site in config.site_names:
            path = self._site_location_path(site)
            if not config.serves_admin_tools(site):
                path.unlink(missing_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_render(site))

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
        return host_bench_dir(self.compose_path.parent) / "sites/common_site_config.json"

    def _get_common_site_config(self) -> dict:
        config_path = self._get_common_site_config_path()
        if not config_path.exists():
            raise BenchException(self.bench_name, message="common_site_config.json not found.")
        return json.loads(config_path.read_bytes())

    def _save_common_site_config(self, config: dict):
        self._get_common_site_config_path().write_text(json.dumps(config))

    def _mailpit_conf(self) -> dict:
        """The three keys that make Frappe fall back to Mailpit for outgoing mail.

        Frappe reads them through `frappe.conf`, the merge of common_site_config.json and the
        site's own site_config.json (site wins), so the same keys work at either scope. They are
        a FALLBACK: a site with a default outgoing Email Account in its DB ignores them.
        """
        return {
            "mail_port": 1025,
            "mail_server": f"{get_container_name_prefix(self.bench_name)}{CLI_DEFAULT_DELIMETER}mailpit",
            "disable_mail_smtp_authentication": 1,
        }

    def _site_config_path(self, site: str) -> Path:
        return host_bench_dir(self.compose_path.parent) / f"sites/{site}/site_config.json"

    def _apply_mailpit_conf(self, config: dict) -> dict:
        for key, value in self._mailpit_conf().items():
            if key not in config or not config[key] == value:
                config[key] = value
        return config

    def _strip_mailpit_conf(self, config: dict) -> dict:
        """Delete only keys still carrying fm's own Mailpit values: a hand-configured mail
        server in the same keys is somebody's real SMTP setup and must survive."""
        for key, value in self._mailpit_conf().items():
            if key in config and config[key] == value:
                del config[key]
        return config

    def configure_mailpit_as_default_server(self):
        self.output.change_head("Configuring Mailpit as default mail server")
        config = self._apply_mailpit_conf(self._get_common_site_config())
        self._save_common_site_config(config)
        self.output.print("Configured Mailpit as default mail server")

    def remove_mailpit_as_default_server(self):
        """Strip the Mailpit fallback everywhere it may live: the common config AND every site's
        own site_config.json. Called when the containers stop -- per-site keys left behind would
        point that site's outgoing mail at a stopped container, silently swallowing it."""
        self.output.change_head("Removing Mailpit as default mail server")
        config = self._strip_mailpit_conf(self._get_common_site_config())
        self._save_common_site_config(config)
        for site in self.bench.bench_config.site_names:
            self.remove_mailpit_for_site(site)
        self.output.print("Removed Mailpit as default mail server")

    def configure_mailpit_for_site(self, site: str):
        """Write the Mailpit fallback into ONE site's site_config.json; the bench's other sites
        and any site created later are untouched (that is what the common-config form is for)."""
        self.output.change_head(f"Configuring Mailpit as {site}'s default mail server")
        config_path = self._site_config_path(site)
        if not config_path.exists():
            raise BenchException(self.bench_name, message=f"sites/{site}/site_config.json not found.")
        config = self._apply_mailpit_conf(json.loads(config_path.read_bytes()))
        config_path.write_text(json.dumps(config))
        self.output.print(f"Configured Mailpit as the default mail server for {site}")

    def remove_mailpit_for_site(self, site: str):
        config_path = self._site_config_path(site)
        if not config_path.exists():
            return
        config = self._strip_mailpit_conf(json.loads(config_path.read_bytes()))
        config_path.write_text(json.dumps(config))

    def wait_till_services_started(self, interval=2, timeout=30):
        """Wait until each admin tool answers THROUGH the bench nginx.

        The probe runs from inside nginx on purpose. Admin tools are never reached directly: every
        request goes browser -> bench nginx -> `fm__<bench>__<tool>` (see
        templates/admin-tools-location.tmpl), so a check from inside the tool's own container
        ("is something bound on my localhost?") can pass while the feature is broken -- a tool off
        the bench network, or a wrong container alias, both answer themselves happily and 502 a
        user. A probe that passes while the feature is broken is worse than one that fails while
        it works.

        The price of probing through nginx is that a dead nginx makes every exec fail, and fm used
        to report that as "Failed to start admin tools" while `docker ps` showed the tools healthy,
        sending the operator after the wrong component (rtCamp/Frappe-Manager#481). Hence the
        explicit nginx check first: it is a different failure and it says so.
        """
        if not self._bench_nginx_running():
            raise AdminToolsProbeUnavailable(self.bench_name)

        admin_tools_services = [
            ("mailpit", "8025"),
            ("adminer", "8080"),
        ]
        prefix = get_container_name_prefix(self.bench_name) + CLI_DEFAULT_DELIMETER

        for tool_name, tool_port in admin_tools_services:
            last_error: DockerException | None = None
            running = False
            for _ in range(timeout):
                try:
                    self.bench.docker_client.compose.exec(
                        service="nginx",
                        command=f"wait-for-it -t {interval} {prefix}{tool_name}:{tool_port}",
                        stream=False,
                    )
                    running = True
                    break
                except DockerException as e:
                    # Kept, not discarded: "the exec could not run" and "the port refused" are
                    # different failures that reach here identically. `enable()` already raises
                    # `from e` for exactly this reason.
                    last_error = e

            if not running:
                raise AdminToolsFailedToStart(
                    self.bench_name,
                    compose_path=self.compose_path,
                    services=[tool_name],
                ) from last_error

    def _bench_nginx_running(self) -> bool:
        """Whether the bench nginx, the only path to the admin tools, is up."""
        return self.bench.docker_ops.get_services_running_status().get("nginx") == "running"

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
