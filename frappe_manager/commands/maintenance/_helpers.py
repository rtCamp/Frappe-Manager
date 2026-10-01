"""Shared vhost, page and domain helpers for the fm maintenance commands."""

import html as html_module
import re
from pathlib import Path

import tomlkit
import typer

from frappe_manager import CLI_BENCH_CONFIG_FILE_NAME, CLI_BENCHES_DIRECTORY
from frappe_manager.services_manager.proxy_dropins import INCLUDE_BEGIN, INCLUDE_END, ProxyDropins
from frappe_manager.site_manager.modules.public_scheme import host_has_trusted_front, public_scheme
from frappe_manager.utils.callbacks import bench_site_callback

_INCLUDE_RE = re.compile(re.escape(INCLUDE_BEGIN) + r".*?" + re.escape(INCLUDE_END) + r"\n?", re.DOTALL)


# Conventioned per-bench custom page: drop this file into the bench's configs
# directory once and every future enable uses it automatically.
_BENCH_PAGE_RELPATH = ("configs", "maintenance.html")

_DEFAULT_MESSAGE = "Scheduled maintenance is in progress. We will be back shortly."

_ALLOW_PATH_RE = re.compile(r"^/[A-Za-z0-9/_.~%-]*\*?$")

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Down for maintenance</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f4f5f6; color: #1f272e; }}
  main {{ text-align: center; padding: 2rem; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 .6rem; }}
  p {{ color: #505a62; margin: 0; }}
</style>
</head>
<body>
<main>
  <h1>Down for maintenance</h1>
  <p>{message}</p>
</main>
</body>
</html>
"""


def _page_filename(bench_name: str) -> str:
    return f"fm-maintenance-{bench_name}.html"


def _vhost_conf(
    bench_name: str,
    token: str,
    html_container_dir: str,
    code: int,
    retry_after: int,
    allow_ips: list[str],
    allow_paths: list[str],
    secure_cookie: bool,
) -> str:
    """Per-domain nginx-proxy maintenance fragment: everyone gets the maintenance page with the
    configured status code; the bypass cookie (or its exact set-URL), allow-listed IPs, and
    allow-listed paths pass through to the bench. /api/* requests receive a JSON body via a
    `rewrite ... last` jump, not a `return <code>` -- and that is why this fragment is ordered
    BEFORE https-redirect in ProxyDropins.ORDER: `return <code>` triggers `error_page`, whose
    internal redirect re-runs this server's `if` directives, so a plain `return` and
    https-redirect's own `return 301` commute regardless of write order; the `/api/` rewrite does
    NOT re-enter, so whichever fragment's `if` runs first wins outright, and with the order
    reversed an API client on plain HTTP would get a 301 redirect instead of this fragment's
    maintenance JSON."""
    allow_lines = []
    for ip in allow_ips:
        allow_lines.append(f'if ($remote_addr = "{ip}") {{\n    set $fm_maintenance 0;\n}}')
    for path in allow_paths:
        if path.endswith("*"):
            allow_lines.append(f'if ($uri ~ "^{re.escape(path[:-1])}") {{\n    set $fm_maintenance 0;\n}}')
        else:
            allow_lines.append(f"if ($uri = {path}) {{\n    set $fm_maintenance 0;\n}}")
    allow_block = ("\n".join(allow_lines) + "\n") if allow_lines else ""

    retry_after_line = f"    add_header Retry-After {retry_after} always;\n" if retry_after > 0 else ""
    secure = "; Secure" if secure_cookie else ""

    return f"""# (bench: {bench_name})
set $fm_maintenance 1;
if ($cookie_fm_maintenance_bypass = "{token}") {{
    set $fm_maintenance 0;
}}
if ($uri = /fm-bypass/{token}) {{
    set $fm_maintenance 0;
}}
if ($uri = /fm-bypass/off) {{
    set $fm_maintenance 0;
}}
if ($uri = /fm-maintenance-page) {{
    set $fm_maintenance 0;
}}
{allow_block}if ($uri ~ ^/api/) {{
    set $fm_maintenance "${{fm_maintenance}}-api";
}}
if ($fm_maintenance = "1-api") {{
    rewrite ^ /fm-maintenance-json last;
}}
if ($fm_maintenance = 1) {{
    return {code};
}}
error_page {code} /fm-maintenance-page;
location = /fm-maintenance-page {{
    internal;
    root {html_container_dir};
{retry_after_line}    try_files /{_page_filename(bench_name)} =502;
}}
location = /fm-maintenance-json {{
    default_type application/json;
{retry_after_line}    return {code} '{{"message":"Service temporarily unavailable for maintenance"}}';
}}
location = /fm-bypass/{token} {{
    add_header Set-Cookie "fm_maintenance_bypass={token}; Path=/; Max-Age=86400; HttpOnly{secure}";
    return 302 /;
}}
location = /fm-bypass/off {{
    add_header Set-Cookie "fm_maintenance_bypass=; Path=/; Max-Age=0{secure}";
    return 302 /;
}}
"""


def _bench_domains(benchname: str, site: str | None = None) -> tuple[list[str], dict[str, bool], list[str]]:
    """(acting domains, per-domain TLS state, every domain the bench serves), read straight from
    bench_config.toml, so maintenance works even when the bench itself is stopped or broken.

    Certificates are per domain: every ``[[ssl.certificates]]`` entry carries its
    own ``ssl_type``, so a bench can serve its primary domain over TLS and an alias
    over plain http. A top-level ``ssl.ssl_type`` would always read as absent,
    because export_to_toml never writes that key.

    ``site`` narrows the ACTING domains to that site's own name and its aliases. The third value is
    always every domain of the bench, and the `--off` orphan sweep needs it rather than the narrowed
    list: the sweep disables any block naming this bench on a domain it no longer serves, so given a
    narrowed list it would read a SIBLING site's live maintenance as an orphan and take it down.
    """
    config_path = CLI_BENCHES_DIRECTORY / benchname / CLI_BENCH_CONFIG_FILE_NAME
    data = tomlkit.parse(config_path.read_text())
    # Raw-TOML read, not BenchConfig: maintenance must keep working on a bench whose config the
    # model would refuse. Aliases live per-site under `[sites]`, not at the top level.
    sites = data.get("sites") or {}
    by_site: dict[str, list[str]] = {}
    for site_name, entry in sites.items():
        name = str(site_name)
        by_site[name] = [name, *(str(alias) for alias in ((entry or {}).get("alias_domains") or []))]
    all_domains = [domain for hostnames in by_site.values() for domain in hostnames]
    if not all_domains:
        # No `[sites]` recorded: a bench that has not been migrated, whose one site is its own name.
        all_domains = [benchname]
        by_site = {benchname: [benchname]}
    domains = by_site.get(site, []) if site else all_domains
    certificates = (data.get("ssl") or {}).get("certificates") or []
    secured = {str(cert.get("domain")) for cert in certificates if str(cert.get("ssl_type", "none")) != "none"}
    return domains, {domain: domain in secured for domain in all_domains}, all_domains


def domain_secure_cookie(services, domain_has_certificate: bool) -> bool:
    """Whether the maintenance bypass cookie gets `Secure` for a domain: true when fm holds a
    certificate for it OR a trusted front terminates TLS in front of it (see
    `site_manager/modules/public_scheme.py`) -- not certificate presence alone, or a fronted
    domain with no fm-side certificate would be handed a cookie the browser only sends over TLS,
    minus the flag that makes it actually arrive."""
    confd_dir = Path(services.proxy_storage.dirs.confd.host)
    return public_scheme(domain_has_certificate, host_has_trusted_front(confd_dir)) == "https"


def _extract_token(conf_text: str) -> str | None:
    match = re.search(r'\$cookie_fm_maintenance_bypass = "([A-Za-z0-9]+)"', conf_text)
    return match.group(1) if match else None


def _extract_code(conf_text: str) -> int:
    match = re.search(r"\$fm_maintenance = 1\) \{\s*return (\d+);", conf_text)
    return int(match.group(1)) if match else 503


def _extract_bench(conf_text: str) -> str:
    match = re.search(r"\(bench: ([^)]+)\)", conf_text)
    return match.group(1) if match else "?"


def _resolve_page_html(benchname: str, page: Path | None, message: str | None) -> str:
    """Custom page resolution, first match wins: --page file, --message into
    the built-in template, the bench's conventioned maintenance.html, then the
    built-in default."""
    if page is not None:
        return page.read_text()
    if message is not None:
        return _PAGE_TEMPLATE.format(message=html_module.escape(message))
    bench_page = CLI_BENCHES_DIRECTORY.joinpath(benchname, *_BENCH_PAGE_RELPATH)
    if bench_page.exists():
        return bench_page.read_text()
    return _PAGE_TEMPLATE.format(message=_DEFAULT_MESSAGE)



def optional_bench_site_callback(ctx: typer.Context, value: str | None):
    """`fm maintenance status` takes no bench and then lists every domain in maintenance, so a
    missing address is valid here rather than prompted for. Every other invocation gets the
    standard prompt/validation, and a BENCH/SITE address still arrives on ctx.obj["site"]."""
    if value is None:
        return None
    return bench_site_callback(ctx, value)


def proxy_paths(ctx):
    """The shared nginx-proxy locations every maintenance verb writes to or reads from."""
    services = ctx.obj["services"]
    html_mount = services.proxy_storage.dirs.html
    vhostd_dir = Path(services.proxy_storage.dirs.vhostd.host)
    return (
        services,
        ProxyDropins.for_services_path(vhostd_dir.parent.parent),
        Path(html_mount.host),
        str(html_mount.container),
    )



def conf_state(dropins: ProxyDropins, domain: str) -> bool:
    """A domain is in fm maintenance only when its own fragment is active: a hand-written vhost.d
    file, or one carrying only a different concern's fragment, is not maintenance and must never
    be reported or stripped as if it were."""
    return "maintenance" in dropins.active(domain)


def _foreign_vhost_content(path: Path) -> bool:
    """Whether a shared vhost.d file carries anything besides fm's own include bootstrap: an
    operator's hand-written directive must never be reported as, or mistaken for, maintenance."""
    if not path.exists():
        return False
    return bool(_INCLUDE_RE.sub("", path.read_text(), count=1))


def _maintenance_domains(dropins: ProxyDropins) -> list[str]:
    # fmd/vhost is keyed by domain, not by bench, so listing every domain in maintenance means
    # walking it directly rather than filtering one bench's own domain list.
    try:
        domain_dirs = sorted(p.name for p in (dropins.fmd_dir / "vhost").iterdir() if p.is_dir())
    except OSError:
        return []
    return [domain for domain in domain_dirs if "maintenance" in dropins.active(domain)]
