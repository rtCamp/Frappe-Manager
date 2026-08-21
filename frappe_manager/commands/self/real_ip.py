import re
from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.modules.realip import (
    CLOUDFLARE_FALLBACK_RANGES,
    CLOUDFLARE_IPS_URLS,
    build_proxy_realip_conf,
    is_fm_realip_conf,
    validate_cidrs,
)

_CONF_FILENAME = "fm-real-ip.conf"

# nginx header names are tokens. Without this an `--header 'X-Real-IP; deny all; #'` lands
# verbatim in `real_ip_header <value>;`, injecting arbitrary directives into the LIVE global
# proxy's conf.d.
_HEADER_TOKEN = re.compile(r"^[A-Za-z0-9-]+$")


def _proxy_conf_is_valid(services) -> bool | None:
    """`nginx -t` inside the live global proxy. ``None`` when it is not running, so there is
    nothing to validate against and nothing to reload."""
    from frappe_manager.docker import DockerException

    if not services.is_service_running("global-nginx-proxy"):
        return None
    try:
        services.docker_client.compose.exec(service="global-nginx-proxy", command="nginx -t", stream=False)
    except DockerException:
        return False
    return True


def _restore_conf(conf_path: Path, previous: str | None) -> None:
    if previous is None:
        conf_path.unlink(missing_ok=True)
    else:
        conf_path.write_text(previous)


def _fetch_cloudflare_ranges(output) -> list[str]:
    """Live ranges from Cloudflare's published lists, vendored fallback when
    the fetch fails (offline hosts must still be able to enable this)."""
    import requests

    ranges: list[str] = []
    try:
        for url in CLOUDFLARE_IPS_URLS:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            ranges += [line.strip() for line in response.text.splitlines() if line.strip()]
        return validate_cidrs(ranges)
    except Exception as e:
        output.warning(f"Could not fetch live Cloudflare ranges ({e}); using the vendored list")
        return list(CLOUDFLARE_FALLBACK_RANGES)


@example(
    "Trust Cloudflare",
    "--cdn cloudflare",
    detail="Proxy logs, fm maintenance --allow-ip and frappe's rate limiting then see the visitor instead of Cloudflare's edge.",
)
@example(
    "Trust your own load balancer",
    "--trust 203.0.113.0/24",
    detail="Each run replaces the whole configuration, so pass every range you sit behind in one call.",
)
@example(
    "Show what is trusted",
    "--status",
)
def real_ip(
    ctx: typer.Context,
    cdn: Annotated[
        str | None,
        typer.Option(
            "--cdn",
            help="Trust a CDN's published ranges. Supported: cloudflare.",
            show_default=False,
        ),
    ] = None,
    trust: Annotated[
        list[str],
        typer.Option(
            "--trust",
            help="CIDR range or single IP of a proxy in front of fm (repeatable).",
            show_default=False,
        ),
    ] = [],
    header: Annotated[
        str | None,
        typer.Option(
            "--header",
            help="Header the client IP is read from. Defaults to CF-Connecting-IP for --cdn cloudflare and X-Forwarded-For otherwise; anything that is not a valid header name is refused.",
            show_default=False,
        ),
    ] = None,
    off: Annotated[
        bool,
        typer.Option("--off", help="Remove the configuration and reload the proxy."),
    ] = False,
    status: Annotated[
        bool,
        typer.Option("--status", help="Show the active configuration. Writes nothing."),
    ] = False,
):
    """
    Restore the visitor's real IP at the global nginx proxy when it sits behind a CDN or load balancer.

    Trust only the ranges you actually sit behind: whatever you trust fully controls the client IP that fm, your logs and frappe go on to see.
    """

    output = get_global_output_handler()
    services = ctx.obj["services"]

    confd_dir = Path(services.proxy_storage.dirs.confd.host)
    conf_path = confd_dir / _CONF_FILENAME

    if off and status:
        output.error("--off cannot be combined with --status", exception=typer.Exit(code=1))

    if header is not None and not _HEADER_TOKEN.match(header):
        # Rejected BEFORE anything is written: this value is rendered verbatim into a file that
        # is bind-mounted into the live global proxy's /etc/nginx/conf.d.
        output.error(
            f"--header {header!r} is not a valid header name (allowed: letters, digits and '-')",
            exception=typer.Exit(code=1),
        )

    if status:
        if conf_path.exists() and is_fm_realip_conf(conf_path.read_text()):
            output.print(f"real-ip configuration active ({conf_path}):")
            for line in conf_path.read_text().splitlines()[1:]:
                output.print(f"  {line}")
        else:
            output.print("No real-ip configuration active on the global proxy")
        return

    if off:
        if conf_path.exists() and is_fm_realip_conf(conf_path.read_text()):
            conf_path.unlink()
            if services.nginx_controller.reload():
                output.print("Removed real-ip configuration; proxy reloaded")
            else:
                output.print("Removed real-ip configuration (the global proxy was not reloaded)")
        else:
            output.print("No real-ip configuration was active")
        return

    if not cdn and not trust:
        output.error(
            "Nothing to trust: pass --cdn cloudflare and/or --trust CIDR (or use --status / --off)",
            exception=typer.Exit(code=1),
        )

    ranges: list[str] = []
    resolved_header = header
    if cdn:
        if cdn.lower() != "cloudflare":
            output.error(
                f"Unsupported CDN {cdn!r}; supported: cloudflare (use --trust for custom ranges)",
                exception=typer.Exit(code=1),
            )
        ranges += _fetch_cloudflare_ranges(output)
        if resolved_header is None:
            resolved_header = "CF-Connecting-IP"
    if trust:
        try:
            ranges += validate_cidrs(trust)
        except ValueError as e:
            output.error(f"--trust: {e}", exception=typer.Exit(code=1))
        if resolved_header is None:
            resolved_header = "X-Forwarded-For"

    if resolved_header is None:  # unreachable: cdn or trust is guaranteed above
        resolved_header = "X-Forwarded-For"

    conf_path.parent.mkdir(parents=True, exist_ok=True)
    previous = conf_path.read_text() if conf_path.exists() else None
    conf_path.write_text(build_proxy_realip_conf(ranges, resolved_header, recursive=True))

    # The file lives in the global proxy's conf.d, so an nginx-invalid file does not just fail
    # here: the proxy refuses to start on its next restart, taking every bench on this host down
    # long after this command returned. Validate, and roll back rather than leave that behind.
    valid = _proxy_conf_is_valid(services)
    if valid is False:
        _restore_conf(conf_path, previous)
        output.error(
            f"nginx rejected the configuration; {_CONF_FILENAME} was rolled back and the proxy left untouched",
            exception=typer.Exit(code=1),
        )

    summary = f"trusting {len(ranges)} range(s), restoring client IP from {resolved_header}"

    if valid is None:
        output.print(f"Real-ip written ({summary}); the global proxy is not running, so it applies on next start")
        return

    if not services.nginx_controller.reload():
        output.warning(
            f"Real-ip written and validated ({summary}), but the proxy did not reload; "
            "run 'fm services restart global-nginx-proxy' to apply it"
        )
        return

    output.print(f"Real-ip active: {summary}")
