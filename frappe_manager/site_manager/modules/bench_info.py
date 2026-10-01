import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from frappe_manager.docker import DockerException
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.proxy_dropins import ProxyDropins
from frappe_manager.site_manager.bench_config import (
    AuthConfig,
    BenchRuntime,
    WebAuthConfig,
    read_default_site,
    read_sites_on_disk,
    resolve_primary_site,
)
from frappe_manager.site_manager.exceptions import BenchException
from frappe_manager.site_manager.modules.maintenance_state import domains_in_maintenance
from frappe_manager.site_manager.modules.public_scheme import host_proxy_state, public_scheme, public_url
from frappe_manager.site_manager.modules.telemetry_state import telemetry_state
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.helpers import format_ssl_certificate_time_remaining
from frappe_manager.utils.site import (
    host_bench_dir,
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



def _root(config) -> Path:
    """The bench DIRECTORY. `root_path` is the bench_config.toml file despite its name."""
    return Path(config.root_path).parent


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
        unmanaged_site_dirs_fn,
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
            unmanaged_site_dirs_fn: Callable returning the site directories on disk that
                `[sites]` does not record. Required, with no default: a default would let a
                construction path that forgets it silently stop reporting drift, and a drift
                report that quietly does not happen is the failure this reporting exists to
                prevent.
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
        self.unmanaged_site_dirs = unmanaged_site_dirs_fn
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
        common_bench_config_path = host_bench_dir(self.bench_path) / "sites/common_site_config.json"
        if not common_bench_config_path.exists():
            raise BenchException(self.bench_name, message="common_site_config.json not found.")
        return json.loads(common_bench_config_path.read_text())

    def get_site_config(self, site: str | None = None) -> dict:
        """
        Get site-specific configuration from site_config.json.

        Args:
            site: which site to read. None means the bench's own site.

        Returns:
            dict: Site configuration

        Raises:
            BenchException: If site_config.json not found
        """
        target = site or self.bench_config.primary_site
        site_config_path = host_bench_dir(self.bench_path) / "sites" / target / "site_config.json"
        if not site_config_path.exists():
            raise BenchException(self.bench_name, message=f"site_config.json not found for site '{target}'.")
        return json.loads(site_config_path.read_text())

    def get_bench_apps(self) -> list[dict]:
        """Installed apps as ``[{name, ref, commit}]``.

        Image runtime: from the baked ``fm.apps`` label (the host has no ``apps/``).
        Mount runtime: from git under the workspace ``apps/``.
        """
        if self.bench_config.runtime == BenchRuntime.image:
            deployments = self.bench_config.deployments
            image = deployments.current.app_image if deployments and deployments.current else None
            if not image or self.docker_client is None:
                return []
            raw = self.docker_client.image_labels(image).get("fm.apps")
            try:
                return json.loads(raw) if raw else []
            except (ValueError, TypeError):
                return []
        return read_bench_app_refs(host_bench_dir(self.bench_path))

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
    def _auth_fact(cls, auth: AuthConfig | WebAuthConfig | None) -> str:
        """Basic auth summary: which nginx surfaces prompt, the credentials, the allow lists.

        ``None`` is a config written before ``[auth]`` existed, so the model defaults
        apply (tools prompt, web does not, password minted on the next start).

        A site's own auth is a :class:`WebAuthConfig`, which has no ``tools``: there is one Adminer
        and one Mailpit per bench, so that surface is only ever the bench's and is reported on the
        bench's row.
        """
        auth = auth or AuthConfig()
        tools = auth.tools if isinstance(auth, AuthConfig) else None
        pairs = (("web", auth.web),) if tools is None else (("web", auth.web), ("tools", tools))
        surfaces = [name for name, enabled in pairs if enabled]
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

    @classmethod
    def _auth_data(cls, auth: AuthConfig | WebAuthConfig | None) -> dict:
        """Structured counterpart of ``_auth_fact``: same facts, no markup, for ``fm info --json``."""
        auth = auth or AuthConfig()
        tools = auth.tools if isinstance(auth, AuthConfig) else None
        pairs = (("web", auth.web),) if tools is None else (("web", auth.web), ("tools", tools))
        surfaces = [name for name, enabled in pairs if enabled]
        return {
            "surfaces": surfaces,
            "user": auth.user if surfaces else None,
            "password": auth.password if surfaces else None,
            "password_pending": bool(surfaces) and not auth.password,
            "allow_ips": list(auth.allow_ips) if surfaces else [],
            "allow_paths": list(auth.allow_paths) if surfaces else [],
        }

    def get_python_version(self) -> str:
        """Active Python version.

        Image runtime: read the ``fm.python.version`` label baked onto the image
        (immutable). Mount runtime: read the uv python-default symlink.
        """
        if self.bench_config.runtime == BenchRuntime.image:
            return self._image_label("fm.python.version")
        return read_bench_python_version(host_bench_dir(self.bench_path)) or "N/A"

    def get_node_version(self) -> str:
        """Active Node version.

        Image runtime: read the ``fm.node.version`` label baked onto the image.
        Mount runtime: read the fnm default alias symlink.
        """
        if self.bench_config.runtime == BenchRuntime.image:
            return self._image_label("fm.node.version")
        return read_bench_node_version(host_bench_dir(self.bench_path)) or "N/A"

    def _image_label(self, key: str) -> str:
        """Read ``key`` off the pinned image (deployments.current.app_image); ``N/A`` if absent."""
        deployments = self.bench_config.deployments
        image = deployments.current.app_image if deployments and deployments.current else None
        if not image or self.docker_client is None:
            return "N/A"
        return self.docker_client.image_labels(image).get(key) or "N/A"

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
        base_log_dir = host_bench_dir(self.bench_path) / "logs"
        if self.bench_config.environment_type.value == "dev":
            bench_dev_server_log_path = base_log_dir / "web.dev.log"
            return [p for p in [bench_dev_server_log_path] if p.exists()]
        bench_prod_server_log_path_stdout = base_log_dir / "web.log"
        bench_prod_server_log_path_stderr = base_log_dir / "web.error.log"
        return [p for p in [bench_prod_server_log_path_stderr, bench_prod_server_log_path_stdout] if p.exists()]

    def _admin_password_for(self, site: str | None, config) -> str:
        """One site's recorded Administrator password, or the bench default labelled as such.

        No site means no config to read: a bench with no `[sites]` entry, or one whose primary
        cannot be named. A recorded site can also have no directory yet. The bench's `admin_pass` is
        then all fm has, and calling it "(default)" is the honest label because it is what the next
        site created will get rather than a password known to work.

        Sites added by `fm create BENCH/SITE` currently record nothing, so they read as the default
        too. That is accurate: `_add_site_to_bench` never writes `admin_password`, and the site was
        created with the bench's value.
        """
        default = config.admin_pass + " (default)"
        if not site:
            return default
        try:
            return self.get_site_config(site).get("admin_password", default)
        except BenchException:
            return default

    def _certificate_rows(self) -> list[dict]:
        """The certificate rows `fm ssl list` enumerates, for this bench.

        Both the card and the `--json` payload reduce from this, so a summary cannot describe a
        different set of certificates than the detail command lists. Local import because the row
        builder lives under `commands/` and single derivation outranks the direction of one import;
        a bench whose certificates cannot be read reports none rather than failing, since `fm info`
        is where an operator goes to find out what is wrong.
        """
        from frappe_manager.commands.ssl.bench_helpers import _bench_certificate_rows

        try:
            return [row for row in _bench_certificate_rows(self, set()) if row["status"] != "none"]
        except Exception:
            return []

    def build_bench_info_data(self) -> dict:
        """Structured facts for ``fm info --json``, gathered independently of ``display_info``'s
        rich card (see ``list_benches_data``/``list_benches_view`` for the shared convention).

        Every value is JSON-safe (str/int/float/bool/None/list/dict); no rich markup, no
        pre-formatted sizes or glyphs -- the card is the presentation layer, this is the facts.
        """
        from frappe_manager.utils.prune import host_prune_settings, parse_size, plan_log_prune, plan_session_prune

        config = self.bench_config
        bench_db_info = self.get_db_connection_info()
        has_cert = self.has_certificate()
        front, http_port, https_port = host_proxy_state()
        protocol = public_scheme(has_cert, front)
        active = self.is_running()

        sites = config.site_names if config.sites else []
        primary = (
            resolve_primary_site(
                config.name, config.sites, read_default_site(_root(config)), read_sites_on_disk(_root(config))
            )
            if sites
            else None
        )
        domain = primary or (sites[0] if sites else self.bench_name)

        https: dict = {"enabled": has_cert, "type": None, "challenge_type": None, "expires_at": None}
        if has_cert:
            ssl_cert = config.get_primary_certificate()
            https["type"] = ssl_cert.ssl_type.value
            if ssl_cert.ssl_type == SUPPORTED_SSL_TYPES.le and isinstance(ssl_cert, LetsencryptSSLCertificate):
                https["challenge_type"] = ssl_cert.challenge_type.value
            https["expires_at"] = self.certificate_manager.get_certificate_expiry().isoformat()
            # The same reduction the card shows, from the same rows `fm ssl list` enumerates.
            # Without it the card said "2 certificates" while this payload described one: the exact
            # drift between two derivations that having one source is meant to prevent.
            rows = self._certificate_rows()
            https["count"] = len(rows)
            https["orphaned"] = sum(1 for row in rows if row.get("orphaned"))
            soonest = min(
                (row["days_until_expiry"] for row in rows if row["days_until_expiry"] is not None), default=None
            )
            https["days_until_expiry"] = soonest

        own_upload_limit_sites = set(config.sites_with_own_upload_limit())
        site_rows = []
        for site in sites:
            database = config.get_database_config(site)
            site_rows.append(
                {
                    "name": site,
                    "primary": site == primary,
                    # The ENGINE, always. `external_database: null` alone could not tell mariadb
                    # from fm's own postgres, so the one fact `--db-type` sets at create was the
                    # one fact info could not report back.
                    "database": config.get_database(site).type.value,
                    # None means the server is fm's own (see display_info).
                    "external_database": {"host": database.host, "port": database.resolved_port} if database else None,
                    # Card and payload share one precedence rule (`effective_upload_limit`): a
                    # second derivation here is exactly how the two surfaces would drift apart.
                    "upload_limit": config.effective_upload_limit(site),
                    "upload_limit_own": site in own_upload_limit_sites,
                }
            )

        aliases = {
            site: sorted((config.sites or {}).get(site).alias_domains)
            for site in sites
            if (config.sites or {}).get(site) and (config.sites or {}).get(site).alias_domains
        }
        missing_site_dirs = (
            [site for site in config.sites if site not in read_sites_on_disk(_root(config))] if config.sites else []
        )
        claimed_domains = sorted(
            p.name.removesuffix(".server.conf")
            for p in (self.bench_path / "configs" / "nginx" / "conf" / "conf.d").glob("*.server.conf")
        )

        # Through the same reader `fm maintenance status` uses, so the two cannot disagree. Without
        # this the headline said `running` for a bench answering 503 to every visitor, which is the
        # one word an operator reads before concluding the site is fine.
        try:
            in_maintenance = domains_in_maintenance(
                ProxyDropins.for_services_path(Path(self.services.proxy_storage.dirs.vhostd.host).parent.parent),
                list(config.domains),
            )
        except Exception:
            # Reporting must survive a proxy fm cannot inspect.
            in_maintenance = []

        apps = [
            {"name": app.get("name"), "ref": app.get("ref"), "commit": app.get("commit")}
            for app in (self.get_bench_apps() or [])
        ]

        deployments = config.deployments if config.runtime == BenchRuntime.image else None
        image = deployments.current.app_image if deployments and deployments.current else None
        previous_image = deployments.previous.app_image if deployments and deployments.previous else None

        deploys = []
        if deployments and deployments.history:
            current_at = deployments.current.deployed_at if deployments.current else None
            current_marked = False
            for entry in reversed(deployments.history):
                # Matched by deployed_at (the record's identity): the same image deployed twice
                # (rollback then forward) must mark only the newest occurrence as current.
                is_current = not current_marked and entry.deployed_at == current_at
                current_marked = current_marked or is_current
                deploys.append(
                    {
                        "app_image": entry.app_image,
                        "deployed_at": entry.deployed_at,
                        "migrate_status": entry.migrate_status,
                        "backup_count": len(entry.backups),
                        "current": is_current,
                    }
                )

        # One row per site: a bench-only bench (no sites) still reports one row keyed by
        # site=None, mirroring the bench-wide fallback `display_info` prints for that case.
        credentialled = sites or [None]
        multi = len(credentialled) > 1
        admin_credentials = [
            {"site": site, "user": "administrator", "password": self._admin_password_for(site, config)}
            for site in credentialled
        ]
        database_credentials = []
        for site in credentialled:
            info = self.get_db_connection_info(site) if multi else bench_db_info
            database_credentials.append({"site": site, "name": info.get("name"), "password": info.get("password")})

        unrouted = [site for site in sites if not config.serves_admin_tools(site)]
        routed = [site for site in sites if config.serves_admin_tools(site)]
        admin_tools = {
            "enabled": bool(config.admin_tools),
            "sites": [
                {
                    "site": site,
                    "mailpit_url": f"{public_url(site, protocol, http_port, https_port)}/mailpit",
                    "adminer_url": f"{public_url(site, protocol, http_port, https_port)}/adminer",
                }
                for site in routed
            ],
            "unrouted_sites": unrouted,
        }

        own_auth = config.sites_with_own_auth
        auth = {
            "bench": self._auth_data(config.auth),
            "sites": {site: self._auth_data(config.sites[site].auth) for site in own_auth},
        }

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

        host_prune = host_prune_settings()
        bench_prune = config.prune

        def _setting(name):
            value = getattr(bench_prune, name, None) if bench_prune else None
            return value if value is not None else getattr(host_prune, name)

        releases_beyond = 0
        if config.runtime == BenchRuntime.image and config.deployments and config.deployments.history:
            keep_releases = config.switch.keep_releases if config.switch else 7
            releases_beyond = max(0, len(config.deployments.history) - keep_releases)

        keep_sessions = int(_setting("keep_backup_sessions"))
        keep_archives = int(_setting("keep_log_archives"))
        over_bytes = parse_size(_setting("rotate_logs_over"))

        stale_backup_sessions = 0
        stale_backup_bytes = 0
        kept_backup_sessions = 0
        for root in (self.bench_path / "backups" / "migrations", self.bench_path / "backups" / "workers"):
            plan = plan_session_prune(root, keep_sessions)
            stale_backup_sessions += plan.count
            stale_backup_bytes += plan.size
            kept_backup_sessions += plan.kept

        log_plan = plan_log_prune(
            [self.bench_path / "workspace" / "frappe-bench" / "logs", self.bench_path / "configs" / "nginx" / "logs"],
            over_bytes,
            keep_archives,
        )

        disk = {
            "releases_beyond_keep": releases_beyond,
            "stale_backup_sessions": stale_backup_sessions,
            "stale_backup_bytes": stale_backup_bytes,
            "kept_backup_sessions": kept_backup_sessions,
            "logs_over_threshold": len(log_plan.rotations),
            "log_rotate_threshold_bytes": over_bytes,
            "log_rotate_bytes": log_plan.rotate_size,
            "actionable": bool(releases_beyond or stale_backup_sessions or log_plan.rotations),
        }

        return {
            "name": self.bench_name,
            "status": "active" if active else "inactive",
            "runtime": config.runtime.value,
            "environment": config.environment_type.value,
            "restart_policy": config.restart_policy.value,
            "maintenance": in_maintenance,
            "telemetry": dict(zip(("enabled", "has_license_key"), telemetry_state(config), strict=True)),
            "url": public_url(domain, protocol, http_port, https_port) if sites else None,
            "https": https,
            "dir": str(self.bench_path.absolute()),
            "sites": site_rows,
            "unmanaged_site_dirs": self.unmanaged_site_dirs(),
            "missing_site_dirs": missing_site_dirs,
            "aliases": aliases,
            "claimed_domains": claimed_domains,
            "python_version": str(self.get_python_version()),
            "node_version": str(self.get_node_version()),
            "apps": apps,
            "image": image,
            "previous_image": previous_image,
            "base_image": config.base_image,
            "apps_from": config.apps_from,
            "deploys": deploys,
            "admin_credentials": admin_credentials,
            "database_credentials": database_credentials,
            "admin_tools": admin_tools,
            "auth": auth,
            "services": {
                "bench": running_bench_services,
                "workers": running_bench_workers,
                "tools": running_bench_admin_tools,
            },
            "disk": disk,
        }

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
        # The mariadb ROOT credentials are deliberately absent: they belong to the shared
        # mariadb container, not to any one bench, and live on `fm services info` now.
        has_cert = self.has_certificate()
        front, http_port, https_port = host_proxy_state()
        protocol = public_scheme(has_cert, front)
        # Same shared reader the data builder and `fm maintenance status` use.
        try:
            in_maintenance = domains_in_maintenance(
                ProxyDropins.for_services_path(Path(self.services.proxy_storage.dirs.vhostd.host).parent.parent),
                list(config.domains),
            )
        except Exception:
            in_maintenance = []
        active = self.is_running()

        # `[sites]` is the record of what this bench serves, so an EMPTY table means zero sites (a
        # `--bench-only` bench, or the last site deleted) rather than one site named after the bench,
        # which is what `site_names` falls back to mid-create.
        #
        # `primary` is None when no recorded site is the bench's own, which is both of the states
        # `fm info` has to survive: nothing recorded at all, and several recorded with none named
        # after the bench. `primary_site` RAISES on the second, and this card is precisely where an
        # operator goes to find out why a bench-scoped command on that bench refuses, so every read
        # below has to print instead. `resolve_primary_site` is that rule's one implementation,
        # shared with the model, so this cannot drift from what `fm shell` decides.
        sites = config.site_names if config.sites else []
        primary = resolve_primary_site(config.name, config.sites, read_default_site(_root(config)), read_sites_on_disk(_root(config))) if sites else None

        # The host the card's link and the admin-tools URLs are built from: the primary when fm can
        # name it, otherwise the first recorded site, and the bench name when there is no site at
        # all. That is precisely what `BenchConfig.domains` publishes as `VIRTUAL_HOST` in each of
        # those three cases, so the URL names a host nginx actually answers on.
        domain = primary or (sites[0] if sites else self.bench_name)

        admin_pass = self._admin_password_for(primary, config)

        # The bench NAME titles the card, because that is what every command takes. Every URL below
        # is a site's DOMAIN, because that is what nginx routes and what a browser can open: a bench
        # `shop` printed `http://shop`, which resolves nowhere, while the site it serves is at
        # `http://shop.localhost`.
        card = railcard.Card(
            self.bench_name,
            railcard.bench_meta(
                active,
                config.runtime.value,
                config.environment_type.value,
                config.restart_policy.value,
                maintenance=bool(in_maintenance),
            ),
            active,
            link=public_url(domain, protocol, http_port, https_port),
        )

        card.section("site")
        if not sites:
            # There is no URL to print, and `http://<bench>` would send the operator to an address
            # that serves nothing. Saying so is what makes a bench-only bench's card useful.
            card.fact("url", "[fm.muted]no site recorded in bench_config.toml[/fm.muted]")
        elif primary is None:
            # fm refuses to guess which site a bench-scoped command means, so the card says why
            # rather than picking one; the rows below carry the addresses that do work.
            card.fact("url", f"[fm.muted]{len(sites)} sites recorded, none named after the bench[/fm.muted]")
        else:
            card.fact("url", public_url(primary, protocol, http_port, https_port))
        if has_cert:
            # Reduced from the SAME rows `fm ssl list` enumerates, so the summary cannot disagree
            # with the detail. Deriving its own answer here meant the card reported the primary
            # site's certificate only: an alias certificate expiring, or one orphaned by
            # `fm domain remove`, was invisible in every summary. Local import: this module is
            # under site_manager and the row builder under commands, and single derivation matters
            # more than the direction of one import.
            rows = self._certificate_rows()

            ssl_cert = config.get_primary_certificate()
            ssl_service_type = f"{ssl_cert.ssl_type.value}"
            if ssl_cert.ssl_type == SUPPORTED_SSL_TYPES.le and isinstance(ssl_cert, LetsencryptSSLCertificate):
                ssl_service_type = f"[{ssl_cert.challenge_type.value}] {ssl_cert.ssl_type.value}"

            soonest = min(
                (row["days_until_expiry"] for row in rows if row["days_until_expiry"] is not None),
                default=None,
            )
            expiry = (
                format_ssl_certificate_time_remaining(self.certificate_manager.get_certificate_expiry())
                if soonest is None
                else f"{soonest} days"
            )
            extra = ""
            if len(rows) > 1:
                orphaned = sum(1 for row in rows if row.get("orphaned"))
                counted = f"{len(rows)} certificates" + (f", {orphaned} orphaned" if orphaned else "")
                extra = f" [fm.muted]·[/fm.muted] {counted} [fm.muted]· fm ssl list {self.bench_name}[/fm.muted]"
            card.fact("https", f"{ssl_service_type.upper()} [fm.muted]·[/fm.muted] {expiry}{extra}")
        elif front:
            # Public TLS without an fm certificate is a real, ongoing half-state, not "off": the
            # front serves visitors fine while the bench's own calls to itself have no certificate
            # to reach. `fm ssl add --dev` is the fix.
            card.fact(
                "https",
                "[fm.muted]your front's · fm holds none, so this bench's own self-calls fail; fm ssl add --dev[/fm.muted]",
            )
        else:
            card.fact("https", "[fm.muted]not enabled[/fm.muted]")
        # One row per site, skipped for the single ordinary case (one site on fm's own mariadb,
        # inheriting the bench's upload limit) because `url` above already names it and its schema
        # is in the `access` section: the common bench's card keeps printing exactly what it always
        # has. Every other shape says something `url` cannot -- more than one site, a schema on a
        # server fm does not own, an engine that is not the default, or a site that set its own
        # upload limit. That last case must trigger the row too: otherwise the one override a site
        # actually enforces is invisible on the one card an operator reads. The engine is read
        # rather than assumed for the same reason: a site on fm's OWN postgres is not external, so
        # a card keyed on externality alone said "mariadb" about a postgres site, and with one site
        # printed no row at all.
        engines = {site: config.get_database(site).type.value for site in sites}
        own_upload_sites = set(config.sites_with_own_upload_limit())
        per_site_rows = bool(sites) and (
            len(sites) > 1
            or config.get_database_config(sites[0]) is not None
            or engines[sites[0]] != "mariadb"
            or bool(own_upload_sites)
        )
        if per_site_rows:
            for i, site in enumerate(sites):
                database = config.get_database_config(site)
                where = (
                    f"external {engines[site]} · {database.host}:{database.resolved_port}"
                    if database
                    else f"fm's {engines[site]}"
                )
                marker = "  [fm.ok]● primary[/fm.ok]" if site == primary else ""
                # Same precedence rule the JSON payload reads (`effective_upload_limit`,
                # `sites_with_own_upload_limit`): a second copy here is how the two would drift.
                limit = config.effective_upload_limit(site)
                owns_limit = "own" if site in own_upload_sites else "inherited"
                upload = f"  [fm.muted]· upload {limit} ({owns_limit})[/fm.muted]"
                url = public_url(site, protocol, http_port, https_port)
                card.fact("sites" if i == 0 else "", f"{url}  [fm.muted]{where}[/fm.muted]{marker}{upload}")
        elif sites:
            # No per-site row rendered, so nothing above has named the limit. It is still the number
            # that decides whether an upload is refused, and an operator who cannot see it anywhere
            # has no way to tell why a 413 happened.
            card.fact("uploads", f"[fm.muted]up to {config.effective_upload_limit(sites[0])}[/fm.muted]")

        # Site directories on disk that `[sites]` does not record (someone ran `bench new-site` by hand
        # inside `fm shell`). Reported, never acted on: fm only destroys a schema it wrote down. Two rows,
        # not one long sentence, to fit the card's 80-column fact width; `fm delete` explains this at
        # length per directory instead, since that's where the schema is about to be destroyed.
        unmanaged = self.unmanaged_site_dirs()
        if unmanaged:
            card.fact("unmanaged", " [fm.muted]·[/fm.muted] ".join(f"sites/{name}/" for name in unmanaged))
            card.fact("", "[fm.muted]not in bench_config.toml; fm will not touch their schemas[/fm.muted]")

        # The MIRROR of the row above: `[sites]` records a site with no directory (removed by hand, or a
        # create failed part-way). A recorded site with no `site_config.json` still gets enumerated,
        # published to nginx and named as a target, though no command can act on it, and it is no longer
        # eligible to be primary. Same two-row shape as above, for the same reason.
        if config.sites:
            missing = [site for site in config.sites if site not in read_sites_on_disk(_root(config))]
            if missing:
                card.fact("missing", " [fm.muted]·[/fm.muted] ".join(f"sites/{name}/" for name in missing))
                card.fact("", "[fm.muted]recorded in bench_config.toml but absent on disk[/fm.muted]")
                # A diagnosis with no prescription: this is what an interrupted `fm delete` leaves,
                # and that same command finishes it (`Bench.remove_site` treats an absent site as
                # the record being all that is left).
                card.fact(
                    "",
                    f"[fm.muted]finish the removal:[/fm.muted] fm delete {config.name}/{missing[0]}",
                )
        # One row per site that has aliases, because a flat list cannot say which hostname reaches
        # which schema. The site goes in the VALUE, not the label: the label column is 14 characters
        # and `aliases of <site>` overruns it, which knocks this card's alignment out. Continuation
        # rows carry an empty label, the same shape the unmanaged row above uses.
        labelled = False
        for site in sites:
            entry = (config.sites or {}).get(site)
            if entry is None or not entry.alias_domains:
                continue
            listed = ", ".join(sorted(entry.alias_domains))
            card.fact("aliases" if not labelled else "", f"[fm.muted]{site}[/fm.muted]  {listed}")
            labelled = True
        # A `conf.d/<domain>.server.conf` makes the entrypoint leave that domain out of its render,
        # so the bench's own settings (auth, admin tools, maintenance) no longer reach it. Invisible
        # otherwise, and months later nobody remembers why one hostname ignores `fm auth`.
        claimed = sorted(
            p.name.removesuffix(".server.conf")
            for p in (self.bench_path / "configs" / "nginx" / "conf" / "conf.d").glob("*.server.conf")
        )
        if claimed:
            card.fact("claimed", ", ".join(claimed))
            card.fact("", "[fm.muted]served by your own conf.d file, not by fm[/fm.muted]")
        abs_path = self.bench_path.absolute()
        card.fact("dir", f"[fm.muted][link=file://{abs_path}]{abs_path}[/link][/fm.muted]")

        card.section("runtime")
        card.fact("python", str(self.get_python_version()))
        card.fact("node", str(self.get_node_version()))
        # Apps: name + ref (branch/tag) + commit. Git-derived; image mode reads labels.
        for i, app in enumerate(self.get_bench_apps() or []):
            label = "apps" if i == 0 else ""
            ref = app.get("ref") or "—"
            commit = app.get("commit") or ""
            card.fact(label, f"{app.get('name', '?')}  [fm.muted]{ref}  {commit}[/fm.muted]")
        # Only when a provider is configured: data leaving the host is headline-relevant, but a
        # line saying "off" on every ordinary bench is noise. "enabled" without a key monitors
        # nothing, so it is reported as the half-state it is rather than as reporting.
        telemetry_enabled, telemetry_has_key = telemetry_state(config)
        if telemetry_enabled or telemetry_has_key:
            if telemetry_enabled and telemetry_has_key:
                card.fact("telemetry", "newrelic [fm.muted]· reporting[/fm.muted]")
            elif telemetry_enabled:
                card.fact("telemetry", "newrelic [fm.muted]· enabled, no license key, not reporting[/fm.muted]")
            else:
                card.fact("telemetry", "newrelic [fm.muted]· license key stored, not enabled[/fm.muted]")
        if config.runtime == BenchRuntime.image:
            deployments = config.deployments
            image = deployments.current.app_image if deployments and deployments.current else None
            card.fact("image", image or "[fm.muted]N/A (not yet deployed)[/fm.muted]")
            if deployments and deployments.previous:
                card.fact("previous", deployments.previous.app_image)
        else:
            if config.base_image:
                card.fact("base", config.base_image)
            if config.apps_from:
                card.fact("apps from", config.apps_from)

        deployments = config.deployments if config.runtime == BenchRuntime.image else None
        if deployments and deployments.history:
            card.section("deploys")
            current_marked = False
            # Matched by `deployed_at` (the record's identity), not `app_image`: the same image
            # deployed twice (a rollback, then forward again) would otherwise mark two rows.
            current_at = deployments.current.deployed_at if deployments.current else None
            for i, entry in enumerate(reversed(deployments.history)):
                label = "history" if i == 0 else ""
                status = entry.migrate_status
                status_markup = f"[fm.error]{status}[/fm.error]" if status == "failed" else status
                # Counted, not just flagged: on a multi-site bench "db-dump" alone would not say
                # whether every site was covered, which is the question a rollback turns on.
                n = len(entry.backups)
                dump = f"  [fm.muted]·[/fm.muted] {n} db-dump{'s' if n > 1 else ''}" if n else ""
                marker = ""
                if not current_marked and entry.deployed_at == current_at:
                    marker = "  [fm.ok]● current[/fm.ok]"
                    current_marked = True
                when = self._short_ts(entry.deployed_at)
                card.fact(label, f"{entry.app_image}  [fm.muted]{when} · {status_markup}{dump}[/fm.muted]{marker}")

        card.section("access")
        # `frappe`/`db` are ONE site's credentials; one row per site, with the site in the VALUE rather
        # than the label -- same treatment as the `aliases` rows above, for the same reason: the label
        # column is 14 characters and a site name overruns it. A single-site bench keeps exactly the row
        # it always printed: nothing to disambiguate, and `url` has already named the site.
        credentialled = sites or [None]
        multi = len(credentialled) > 1
        for i, site in enumerate(credentialled):
            named = f"[fm.muted]{site}[/fm.muted]  " if multi else ""
            card.fact(
                "frappe" if i == 0 else "",
                f"{named}administrator [fm.muted]/[/fm.muted] {self._admin_password_for(site, config)}",
            )
        for i, site in enumerate(credentialled):
            info = self.get_db_connection_info(site) if multi else bench_db_info
            named = f"[fm.muted]{site}[/fm.muted]  " if multi else ""
            db_name = info.get("name", "N/A")
            db_pass = info.get("password", "N/A")
            card.fact(
                "db" if i == 0 else "",
                f"{named}{db_name} [fm.muted]/[/fm.muted] [fm.secret]{db_pass}[/fm.secret]",
            )
        unrouted = [site for site in sites if not config.serves_admin_tools(site)]
        routed = [site for site in sites if config.serves_admin_tools(site)]

        def _tools_url(host: str) -> str:
            base = public_url(host, protocol, http_port, https_port)
            return f"{base}/mailpit [fm.muted]·[/fm.muted] {base}/adminer"

        if not config.admin_tools:
            card.fact("tools", "[fm.muted]not enabled[/fm.muted]")
        elif not unrouted:
            # Every site routes them, so one URL is the whole truth and the card reads as it always
            # did on the benches that never touched per-site routing.
            card.fact("tools", _tools_url(domain))
        elif not routed:
            card.fact("tools", "[fm.muted]running, but no site routes them[/fm.muted]")
        else:
            # Printing one URL here would name a hostname that answers 404 for half the bench.
            for i, site in enumerate(routed):
                card.fact("tools" if i == 0 else "", f"[fm.muted]{site}[/fm.muted]  {_tools_url(site)}")
            card.fact("", f"[fm.muted]not served on {', '.join(unrouted)}[/fm.muted]")

        # The web surface's auth can be per site; the tools surface's auth is always the bench's.
        own_auth = config.sites_with_own_auth
        if not own_auth:
            card.fact("auth", self._auth_fact(config.auth))
        else:
            # A single row would report one site's password as if it opened the others.
            card.fact("auth", f"[fm.muted]bench[/fm.muted]  {self._auth_fact(config.auth)}")
            for site in own_auth:
                card.fact("", f"[fm.muted]{site}[/fm.muted]  {self._auth_fact(config.sites[site].auth)}")

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

        from frappe_manager.utils.prune import host_prune_settings, parse_size, summarize_disk_status

        host_prune = host_prune_settings()
        bench_prune = config.prune

        def _setting(name):
            value = getattr(bench_prune, name, None) if bench_prune else None
            return value if value is not None else getattr(host_prune, name)

        releases_beyond = 0
        if config.runtime == BenchRuntime.image and config.deployments and config.deployments.history:
            keep_releases = config.switch.keep_releases if config.switch else 7
            releases_beyond = max(0, len(config.deployments.history) - keep_releases)

        summary, actionable = summarize_disk_status(
            session_roots=[self.bench_path / "backups" / "migrations", self.bench_path / "backups" / "workers"],
            log_dirs=[
                self.bench_path / "workspace" / "frappe-bench" / "logs",
                self.bench_path / "configs" / "nginx" / "logs",
            ],
            keep_sessions=int(_setting("keep_backup_sessions")),
            keep_archives=int(_setting("keep_log_archives")),
            over_bytes=parse_size(_setting("rotate_logs_over")),
            releases_beyond=releases_beyond,
        )
        card.section("disk")
        if actionable:
            card.fact("status", f"{summary}  [fm.info]fm prune {self.bench_name}[/fm.info]")
        else:
            card.fact("status", f"[fm.muted]{summary}[/fm.muted]")

        # `wants_structured_data` (JSONOutputHandler, and the --json flag through the logging
        # wrapper) asks for facts instead of the card: a memory-address repr otherwise, since
        # a rich Group/Table has no __str__ for json.dumps(default=str) to fall back on.
        if self.output.wants_structured_data:
            self.output.print_data(self.build_bench_info_data())
        else:
            self.output.print_data(card.render())
