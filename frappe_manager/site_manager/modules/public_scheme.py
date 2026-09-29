"""Is a domain served over public HTTPS -- from the Python side.

`$fm_client_scheme` (see `realip.py`) answers this for the nginx redirect. But fm also answers
"is this domain HTTPS?" from Python, in three places (HSTS gating, the maintenance bypass
cookie's `Secure` flag, printed URLs) -- each historically derived it from "does fm hold a
certificate for this domain", which understates TLS the moment a trusted front holds the
certificate instead: the connection to the visitor is TLS, fm just never terminated it. A domain
is public-HTTPS if fm holds a certificate for it OR a trusted front is configured -- the same
either/or `fm-forwarded-trust.conf` encodes for nginx.
"""

from pathlib import Path

from frappe_manager.site_manager.modules.realip import trusted_ranges


def host_has_trusted_front(confd_dir: Path) -> bool:
    """True when this host trusts ANY peer as a front -- the same test `$fm_client_scheme`
    is built from, read straight off the rendered conf so there is one source of truth."""
    return bool(trusted_ranges(confd_dir))


def public_scheme(domain_has_certificate: bool, has_trusted_front: bool) -> str:
    """"https" when fm holds a certificate for the domain OR a trusted front terminates TLS for
    it, else "http". Pure and two-input so every call site supplies its own certificate check
    and reuses one `host_has_trusted_front` read per command instead of re-reading conf.d per
    domain."""
    return "https" if domain_has_certificate or has_trusted_front else "http"


def public_url(domain: str, scheme: str, http_port: int = 80, https_port: int = 443) -> str:
    """The address a browser can open, port included only when fm does not publish on the default.

    Printed URLs are the one place a moved port MUST appear: everything else fm does internally
    still talks to the proxy on 80/443 (see `FMProxyConfig`), but a human pasting the URL reaches
    the host, where only the published port exists.
    """
    port = https_port if scheme == "https" else http_port
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{domain}" if port == default else f"{scheme}://{domain}:{port}"


def host_proxy_state() -> tuple[bool, int, int]:
    """`(has_trusted_front, http_port, https_port)` for this host, read once per command.

    For call sites with no services manager in reach (the info cards), which would otherwise
    have to construct one just to learn where the proxy publishes.
    """
    from frappe_manager import CLI_SERVICES_DIRECTORY
    from frappe_manager.metadata_manager import FMConfigManager

    proxy = FMConfigManager.import_from_toml().proxy
    confd = CLI_SERVICES_DIRECTORY / "nginx-proxy" / "confd"
    return host_has_trusted_front(confd), proxy.http_port, proxy.https_port
