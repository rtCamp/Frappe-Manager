"""
BenchInfo Module

Handles information retrieval and display for the bench including:
- Displaying comprehensive bench information
- Reading config files (common_site_config.json, site_config.json)
- Getting installed apps list
- Getting log file paths
"""

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from frappe_manager.docker import DockerException
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import AuthConfig, BenchRuntime
from frappe_manager.site_manager.exceptions import BenchException
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.helpers import format_ssl_certificate_time_remaining
from frappe_manager.utils.site import (
    read_bench_app_refs,
    read_bench_node_version,
    read_bench_python_version,
)

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
        docker_client=None,
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
        self.docker_client = docker_client
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

    def get_bench_apps(self) -> list[dict]:
        """Installed apps as ``[{name, ref, commit}]``.

        Image runtime: from the baked ``fm.apps`` label (the host has no ``apps/``).
        Mount runtime: from git under the workspace ``apps/``.
        """
        if self.bench_config.runtime == BenchRuntime.image:
            tag = self.bench_config.deploy_state.current_tag if self.bench_config.deploy_state else None
            if not tag or self.docker_client is None:
                return []
            raw = self.docker_client.image_labels(tag).get("fm.apps")
            try:
                return json.loads(raw) if raw else []
            except (ValueError, TypeError):
                return []
        return read_bench_app_refs(self.bench_path / "workspace" / "frappe-bench")

    @staticmethod
    def _short_ts(iso: str) -> str:
        """ISO deploy timestamp -> 'YYYY-MM-DD HH:MM' (raw string on parse failure)."""
        try:
            return datetime.fromisoformat(str(iso)).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return str(iso)

    @staticmethod
    def _compact_list(label: str, items: list[str], limit: int = 3) -> str:
        """``label a, b +2`` so a long allow list still fits on one line ('' when empty)."""
        if not items:
            return ""
        extra = len(items) - limit
        shown = ", ".join(items[:limit])
        return f"{label} {shown}" + (f" +{extra}" if extra > 0 else "")

    @classmethod
    def _auth_fact(cls, auth: AuthConfig | None) -> str:
        """Basic auth summary: which nginx surfaces prompt, the credentials, the allow lists.

        ``None`` is a config written before ``[auth]`` existed, so the model defaults
        apply (tools prompt, web does not, password minted on the next start).
        """
        auth = auth or AuthConfig()
        surfaces = [name for name, enabled in (("web", auth.web), ("tools", auth.tools)) if enabled]
        if not surfaces:
            return "[fm.muted]off[/fm.muted]"
        if auth.password:
            creds = f"{auth.user} [fm.muted]/[/fm.muted] [fm.secret]{auth.password}[/fm.secret]"
        else:
            creds = f"{auth.user} [fm.muted]/ password minted on next start[/fm.muted]"
        extras = [
            part
            for part in (cls._compact_list("allow", auth.allow_ips), cls._compact_list("open", auth.allow_paths))
            if part
        ]
        tail = f"  [fm.muted]· {' · '.join(extras)}[/fm.muted]" if extras else ""
        return f"[fm.ok]{' + '.join(surfaces)}[/fm.ok]  [fm.muted]·[/fm.muted] {creds}{tail}"

    def get_python_version(self) -> str:
        """Active Python version.

        Image runtime: read the ``fm.python.version`` label baked onto the image
        (immutable). Mount runtime: read the uv python-default symlink.
        """
        if self.bench_config.runtime == BenchRuntime.image:
            return self._image_label("fm.python.version")
        return read_bench_python_version(self.bench_path / "workspace/frappe-bench") or "N/A"

    def get_node_version(self) -> str:
        """Active Node version.

        Image runtime: read the ``fm.node.version`` label baked onto the image.
        Mount runtime: read the fnm default alias symlink.
        """
        if self.bench_config.runtime == BenchRuntime.image:
            return self._image_label("fm.node.version")
        return read_bench_node_version(self.bench_path / "workspace/frappe-bench") or "N/A"

    def _image_label(self, key: str) -> str:
        """Read ``key`` off the pinned image (deploy_state.current_tag); ``N/A`` if absent."""
        tag = self.bench_config.deploy_state.current_tag if self.bench_config.deploy_state else None
        if not tag or self.docker_client is None:
            return "N/A"
        return self.docker_client.image_labels(tag).get(key) or "N/A"

    def get_log_file_paths(self) -> list[Path]:
        """
        Get log file paths based on environment type.

        Only paths that exist on the host are returned: the expected file is absent
        whenever the web program has not run yet in this environment (fresh bench,
        dev->prod switch before the first prod start) or the log was rotated away,
        and the caller opens every path it gets. An empty list is the caller's
        "No log files found" case.

        Returns:
            list: List of existing log file paths
        """
        base_log_dir = self.bench_path / "workspace/frappe-bench/logs"
        if self.bench_config.environment_type.value == "dev":
            bench_dev_server_log_path = base_log_dir / "web.dev.log"
            return [p for p in [bench_dev_server_log_path] if p.exists()]
        bench_prod_server_log_path_stdout = base_log_dir / "web.log"
        bench_prod_server_log_path_stderr = base_log_dir / "web.error.log"
        return [p for p in [bench_prod_server_log_path_stderr, bench_prod_server_log_path_stdout] if p.exists()]

    def display_info(self) -> None:
        """Render the bench detail card.

        Same grammar as ``fm list`` (``output_manager.railcard.Card``): the
        list card EXPANDED with site / runtime / access / services sections.
        Layout comes from the active STYLE, colors from the THEME tokens.
        """
        from frappe_manager.output_manager import railcard

        self.output.change_head("Getting bench info")

        config = self.bench_config
        bench_db_info = self.get_db_connection_info()
        services_db_info = self.services.database_manager.database_server_info
        protocol = "https" if self.has_certificate() else "http"
        active = self.is_running()

        admin_pass = config.admin_pass + " (default)"
        site_config = self.get_site_config()
        if "admin_password" in site_config:
            admin_pass = site_config["admin_password"]

        card = railcard.Card(
            self.bench_name,
            railcard.bench_meta(
                active, config.runtime.value, config.environment_type.value, config.restart_policy.value
            ),
            active,
            link=f"{protocol}://{self.bench_name}",
        )

        # ---- site
        card.section("site")
        card.fact("url", f"{protocol}://{self.bench_name}")
        if self.has_certificate():
            ssl_cert = config.get_primary_certificate()
            ssl_service_type = f"{ssl_cert.ssl_type.value}"
            if ssl_cert.ssl_type == SUPPORTED_SSL_TYPES.le and isinstance(ssl_cert, LetsencryptSSLCertificate):
                ssl_service_type = f"[{ssl_cert.challenge_type.value}] {ssl_cert.ssl_type.value}"
            remaining = format_ssl_certificate_time_remaining(self.certificate_manager.get_certificate_expiry())
            card.fact("https", f"{ssl_service_type.upper()} [fm.muted]·[/fm.muted] {remaining}")
        else:
            card.fact("https", "[fm.muted]not enabled[/fm.muted]")
        if config.alias_domains:
            card.fact("domains", ", ".join(sorted(config.alias_domains)))
        abs_path = self.bench_path.absolute()
        card.fact("dir", f"[fm.muted][link=file://{abs_path}]{abs_path}[/link][/fm.muted]")

        # ---- runtime
        card.section("runtime")
        card.fact("python", str(self.get_python_version()))
        card.fact("node", str(self.get_node_version()))
        # Apps: name + ref (branch/tag) + commit. Git-derived; image mode reads labels.
        for i, app in enumerate(self.get_bench_apps() or []):
            label = "apps" if i == 0 else ""
            ref = app.get("ref") or "—"
            commit = app.get("commit") or ""
            card.fact(label, f"{app.get('name', '?')}  [fm.muted]{ref}  {commit}[/fm.muted]")
        if config.runtime == BenchRuntime.image:
            deploy_state = config.deploy_state
            tag = deploy_state.current_tag if deploy_state and deploy_state.current_tag else None
            card.fact("tag", tag or "[fm.muted]N/A (not yet deployed)[/fm.muted]")
            if deploy_state and deploy_state.previous_tag:
                card.fact("previous", deploy_state.previous_tag)
        else:
            if config.base_image:
                card.fact("base", config.base_image)
            if config.seed_image:
                card.fact("seeded", config.seed_image)

        # ---- deploys (image deploy history, newest first)
        deploy_state = config.deploy_state if config.runtime == BenchRuntime.image else None
        if deploy_state and deploy_state.history:
            card.section("deploys")
            current_marked = False
            for i, entry in enumerate(reversed(deploy_state.history)):
                label = "history" if i == 0 else ""
                status = entry.migrate_status
                status_markup = f"[fm.error]{status}[/fm.error]" if status == "failed" else status
                dump = "  [fm.muted]·[/fm.muted] db-dump" if entry.backup else ""
                marker = ""
                if not current_marked and entry.tag == deploy_state.current_tag:
                    marker = "  [fm.ok]● current[/fm.ok]"
                    current_marked = True
                when = self._short_ts(entry.deployed_at)
                card.fact(label, f"{entry.tag}  [fm.muted]{when} · {status_markup}{dump}[/fm.muted]{marker}")

        # ---- access
        card.section("access")
        card.fact("frappe", f"administrator [fm.muted]/[/fm.muted] {admin_pass}")
        db_name = bench_db_info.get("name", "N/A")
        db_pass = bench_db_info.get("password", "N/A")
        card.fact("db", f"{db_name} [fm.muted]/[/fm.muted] [fm.secret]{db_pass}[/fm.secret]")
        card.fact(
            "root db",
            f"{services_db_info.user} [fm.muted]/[/fm.muted] [fm.secret]{services_db_info.password}[/fm.secret] "
            f"[fm.muted]@[/fm.muted] {services_db_info.host}",
        )
        if config.admin_tools:
            card.fact(
                "tools",
                f"{protocol}://{self.bench_name}/mailpit [fm.muted]·[/fm.muted] {protocol}://{self.bench_name}/adminer",
            )
        else:
            card.fact("tools", "[fm.muted]not enabled[/fm.muted]")
        card.fact("auth", self._auth_fact(config.auth))

        # ---- services (live container state)
        def dots(statuses: dict) -> str:
            return "   ".join(f"{railcard.status_dot(state)} {svc}" for svc, state in sorted(statuses.items()))

        running_bench_services = self.get_services_running_status()

        try:
            containers = self.workers.compose_file_manager.get_container_names().values()
            all_statuses = self.workers.docker_client.compose.get_all_services_status()
            running_bench_workers = {
                status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
            }
        except DockerException:
            running_bench_workers = {}

        running_bench_admin_tools = {}
        if self.admin_tools.compose_file_manager.exists():
            try:
                containers = self.admin_tools.compose_file_manager.get_container_names().values()
                all_statuses = self.admin_tools.docker_client.compose.get_all_services_status()
                running_bench_admin_tools = {
                    status["Service"]: status["State"] for status in all_statuses if status.get("Name") in containers
                }
            except Exception:
                running_bench_admin_tools = {}

        if running_bench_services or running_bench_workers or running_bench_admin_tools:
            card.section("services")
            if running_bench_services:
                card.fact("bench", dots(running_bench_services))
            if running_bench_workers:
                card.fact("workers", dots(running_bench_workers))
            if running_bench_admin_tools:
                card.fact("tools", dots(running_bench_admin_tools))

        # Themed singleton console via the handler (no raw Console() bypass).
        self.output.print_data(card.render())
