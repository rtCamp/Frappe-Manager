"""
Manages standalone nginx server blocks for external domains without backends.

This module creates static nginx configurations for domains that use FM's SSL
management but don't have Frappe benches as backends. It handles:
- Creating server blocks for HTTP-01 ACME challenges
- Serving .well-known/acme-challenge directory
- Providing placeholder responses until backend is connected
"""

from pathlib import Path

# fm owns only the files carrying this marker inside the SHARED nginx-proxy conf.d, the same
# discipline as `# fm:auth` and `# fm:maintenance`: docker-gen's default.conf and any hand-written
# conf in that directory are foreign and must never be read as fm's or deleted by it.
STANDALONE_MARKER = "# fm:standalone"

# Pre-1.0 standalone configs carried this header and were named `<domain>.conf`. Recognised so the
# reconcile can migrate them; never written any more.
LEGACY_MARKER = "# Standalone domain:"

# nginx includes conf.d/*.conf in ALPHABETICAL order and, for two server blocks with the same
# server_name, keeps the FIRST and only warns. These blocks are placeholders for a domain with no
# backend yet, so they must lose to docker-gen's real vhost the moment a VIRTUAL_HOST container
# appears -- which means sorting after `default.conf`, whatever the domain is called. Under the old
# `<domain>.conf` name an `api.`/`app.`/`assets.` domain sorted BEFORE default.conf and the 503
# placeholder shadowed the real backend permanently.
FILENAME_PREFIX = "zz-fm-standalone-"


class StandaloneNginxConfigManager:
    """
    Manages nginx server block configurations for external (standalone) domains.

    External domains are those that use FM's SSL infrastructure but don't have
    Frappe benches. Since nginx-proxy only generates configs for containers with
    VIRTUAL_HOST, we need to manually create server blocks for these domains.

    This enables:
    - HTTP-01 ACME challenge support (serves .well-known/acme-challenge)
    - SSL certificate installation without requiring a backend container
    - Graceful handling when backend isn't connected yet

    Attributes:
        conf_dir: Directory where standalone nginx configs are stored
        webroot_dir: Path to webroot for ACME challenges (container path)
        certs_dir: Path to SSL certificates directory (container path)
    """

    HTTP_SERVER_TEMPLATE = """{marker} {domain}
# Managed by Frappe Manager
# This configuration allows HTTP-01 ACME challenge for SSL certificate generation

server {{
    server_name {domain};
    listen 80;
    access_log /var/log/nginx/access.log;
    
    # Serve ACME challenge files for Let's Encrypt validation
    location ^~ /.well-known/acme-challenge/ {{
        default_type "text/plain";
        root {webroot_dir};
    }}
    
    # Default response for all other requests
    location / {{
        return 503 '<html><head><title>503 Service Unavailable</title></head><body><h1>503 Service Unavailable</h1><p>This site is not available.</p></body></html>';
        # default_type, NOT add_header: add_header applies to 2xx/3xx only unless marked `always`,
        # so the placeholder went out as application/octet-stream and browsers downloaded it
        # instead of rendering it.
        default_type text/html;
    }}
}}
"""

    HTTPS_SERVER_TEMPLATE = """{marker} {domain}
# Managed by Frappe Manager
# This configuration provides SSL termination without requiring a backend

server {{
    server_name {domain};
    listen 80;
    access_log /var/log/nginx/access.log;
    
    # Serve ACME challenge files for Let's Encrypt validation
    location ^~ /.well-known/acme-challenge/ {{
        default_type "text/plain";
        root {webroot_dir};
    }}
    
    # Redirect all other HTTP traffic to HTTPS
    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    server_name {domain};
    listen 443 ssl;
    http2 on;
    access_log /var/log/nginx/access.log;
    
    ssl_session_timeout 5m;
    ssl_session_cache shared:SSL:50m;
    ssl_session_tickets off;
    
    ssl_certificate {certs_dir}/{domain}.crt;
    ssl_certificate_key {certs_dir}/{domain}.key;
    
    # Serve ACME challenge files (for renewals)
    location ^~ /.well-known/acme-challenge/ {{
        default_type "text/plain";
        root {webroot_dir};
    }}
    
    # Default response for all other requests
    location / {{
        return 503 '<html><head><title>503 Service Unavailable</title></head><body><h1>503 Service Unavailable</h1><p>This site is not available.</p></body></html>';
        # default_type, NOT add_header: add_header applies to 2xx/3xx only unless marked `always`,
        # so the placeholder went out as application/octet-stream and browsers downloaded it
        # instead of rendering it.
        default_type text/html;
    }}
}}
"""

    def __init__(self, conf_dir: Path, webroot_dir_container: str, certs_dir_container: str):
        """
        Initialize the standalone nginx config manager.

        Args:
            conf_dir: Directory to store standalone nginx configs (host filesystem)
            webroot_dir_container: Path to webroot in container (/usr/share/nginx/html)
            certs_dir_container: Path to certs in container (/etc/nginx/certs)
        """
        self.conf_dir = conf_dir
        self.webroot_dir = webroot_dir_container
        self.certs_dir = certs_dir_container

        self.conf_dir.mkdir(parents=True, exist_ok=True)

    def config_path(self, domain: str) -> Path:
        """Where this domain's config is written today."""
        return self.conf_dir / f"{FILENAME_PREFIX}{domain}.conf"

    def legacy_config_path(self, domain: str) -> Path:
        """Where a pre-1.0 fm wrote it. Only ever read or deleted, never written."""
        return self.conf_dir / f"{domain}.conf"

    def owns(self, path: Path) -> bool:
        """Whether this file is fm's to read and delete.

        Decided by the marker, not the filename: the directory is shared with docker-gen's
        default.conf and with whatever an operator put there, and deleting a foreign vhost because
        its name matched a domain would take down a service fm does not manage.
        """
        if not path.is_file():
            return False
        try:
            head = path.read_text()[:200]
        except OSError:
            return False
        return head.startswith((STANDALONE_MARKER, LEGACY_MARKER))

    def managed_configs(self) -> dict[str, Path]:
        """Every standalone vhost fm owns, by domain, including legacy-named ones.

        Scanned from disk rather than from external_domains.toml, so a config written by an `add`
        that was interrupted before it registered the domain is still visible -- that orphan is
        serving a 503 for a real hostname and nothing else can find it.
        """
        found: dict[str, Path] = {}
        if not self.conf_dir.is_dir():
            return found
        for path in sorted(self.conf_dir.iterdir()):
            if not self.owns(path):
                continue
            domain = self._domain_of(path)
            # A legacy file and a current one for the same domain can coexist until the reconcile
            # migrates them; the current name wins so the caller acts on the one nginx loads last.
            if domain and (domain not in found or path.name.startswith(FILENAME_PREFIX)):
                found[domain] = path
        return found

    def _domain_of(self, path: Path) -> str | None:
        """The domain named by the marker line, which is always the file's first line."""
        first = path.read_text().split("\n", 1)[0].strip()
        for marker in (STANDALONE_MARKER, LEGACY_MARKER):
            if first.startswith(marker):
                return first[len(marker) :].strip() or None
        return None

    def config_state(self, domain: str) -> str | None:
        """None when no config exists, else "http" (challenge-only placeholder) or "https"."""
        for path in (self.config_path(domain), self.legacy_config_path(domain)):
            if self.owns(path):
                return "https" if "listen 443" in path.read_text() else "http"
        return None

    def render(self, domain: str, *, https: bool) -> str:
        """The exact content this domain's config should have.

        Exposed so the reconcile can compare against what is on disk. Comparing only the KIND
        (http vs https) would pin every existing standalone vhost to the template it was first
        written with, so a change to the placeholder page -- or to the challenge location -- would
        never reach a domain already configured.
        """
        if https:
            return self.HTTPS_SERVER_TEMPLATE.format(
                marker=STANDALONE_MARKER,
                domain=domain,
                webroot_dir=self.webroot_dir,
                certs_dir=self.certs_dir,
            )
        return self.HTTP_SERVER_TEMPLATE.format(
            marker=STANDALONE_MARKER,
            domain=domain,
            webroot_dir=self.webroot_dir,
        )

    def _write(self, domain: str, content: str) -> Path:
        config_file = self.config_path(domain)
        config_file.write_text(content)
        # A pre-1.0 install has the same vhost under `<domain>.conf`. Left in place it is a second
        # server block for the same server_name, and the one nginx keeps.
        legacy = self.legacy_config_path(domain)
        if legacy != config_file and self.owns(legacy):
            legacy.unlink()
        return config_file

    def create_http_config(self, domain: str) -> Path:
        """
        Create HTTP-only nginx config for a standalone domain.

        This is used during initial certificate generation to serve ACME challenges.
        After certificate is generated, call create_https_config() to enable SSL.

        Args:
            domain: Domain name

        Returns:
            Path to created config file
        """
        return self._write(domain, self.render(domain, https=False))

    def create_https_config(self, domain: str) -> Path:
        """
        Create HTTPS nginx config for a standalone domain.

        This should be called after the SSL certificate is successfully generated.
        It creates a full server block with SSL termination and HTTP→HTTPS redirect.

        Args:
            domain: Domain name

        Returns:
            Path to created config file
        """
        return self._write(domain, self.render(domain, https=True))

    def remove_config(self, domain: str) -> bool:
        """
        Remove nginx config for a standalone domain.

        Args:
            domain: Domain name

        Returns:
            True if config was removed, False if it didn't exist
        """
        removed = False
        for path in (self.config_path(domain), self.legacy_config_path(domain)):
            if self.owns(path):
                path.unlink()
                removed = True
        return removed


def reconcile_standalone_configs(
    manager: StandaloneNginxConfigManager,
    certificates: dict[str, bool],
) -> list[str]:
    """Rewrite the standalone vhosts that are missing, stale or legacy-named. Returns the domains changed.

    These configs were written once by `fm ssl add --standalone` and then owned by nobody:
    fm_headers.conf is rewritten on every start and default.conf is regenerated by docker-gen, but
    a standalone vhost that went missing (recreated conf.d, restored services directory) stayed
    missing. The domain then serves nginx-proxy's default 503 AND, because the ACME challenge
    location lives in that block, its HTTP-01 renewal can never succeed again -- while the
    certificate on disk keeps `fm ssl list --standalone` reporting a healthy "Renewal OK".

    `certificates` maps each registered domain to whether its certificate files exist. A domain
    without them MUST get the HTTP-only block: an HTTPS block pointing at absent `ssl_certificate`
    files is a fatal nginx config error that takes down every bench the shared proxy fronts.
    """
    changed: list[str] = []
    for domain, has_certificate in certificates.items():
        desired = manager.render(domain, https=has_certificate)
        path = manager.config_path(domain)
        # Compared by CONTENT, not by "does an https block exist": otherwise every domain stays
        # pinned to the template it was first written with, and a change to the placeholder page or
        # the challenge location never reaches a domain that is already configured.
        if path.is_file() and path.read_text() == desired and not manager.owns(manager.legacy_config_path(domain)):
            continue
        if has_certificate:
            manager.create_https_config(domain)
        else:
            manager.create_http_config(domain)
        changed.append(domain)
    return changed
