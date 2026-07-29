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
    detail="The global proxy restores the visitor's real IP from CF-Connecting-IP for requests arriving from Cloudflare's published ranges (fetched live, vendored fallback). Logs, fm maintenance --allow-ip, and frappe rate limiting then see real client IPs.",
)
@example(
    "Trust a custom load balancer",
    "--trust 203.0.113.0/24",
    detail="Restores the client IP from X-Forwarded-For for requests arriving from the given ranges (repeatable). Only list proxies you control: a trusted source controls the IP you see.",
)
@example(
    "Show or remove the configuration",
    "--status",
    detail="Shows the active real-ip configuration; --off removes it and reloads the proxy without downtime.",
)
def real_ip(
    ctx: typer.Context,
    cdn: Annotated[
        str | None,
        typer.Option(
            "--cdn",
            help="Trust a known CDN's published ranges. Supported: cloudflare (uses the CF-Connecting-IP header).",
            show_default=False,
        ),
    ] = None,
    trust: Annotated[
        list[str],
        typer.Option(
            "--trust",
            help="CIDR range (or single IP) of a proxy/LB in front of fm to trust (repeatable). Client IP is taken from X-Forwarded-For.",
            show_default=False,
        ),
    ] = [],
    header: Annotated[
        str | None,
        typer.Option(
            "--header",
            help="Override the header the client IP is restored from (default: CF-Connecting-IP for --cdn cloudflare, X-Forwarded-For otherwise).",
            show_default=False,
        ),
    ] = None,
    off: Annotated[
        bool,
        typer.Option("--off", help="Remove the real-ip configuration from the global proxy."),
    ] = False,
    status: Annotated[
        bool,
        typer.Option("--status", help="Show the active real-ip configuration without changing anything."),
    ] = False,
):
    """
    Restore real client IPs at the global nginx proxy when it sits behind a CDN or load balancer.

    Without this, everything behind a CDN appears to come from the CDN's edge IPs: proxy logs, fm maintenance --allow-ip, and frappe's per-IP rate limiting all see the edge instead of the visitor. This writes an nginx real_ip configuration trusting exactly the given ranges and reloads the proxy without downtime.

    Only trust ranges you actually sit behind: any trusted source fully controls the client IP you observe. The bench-level half (bench nginx trusting fm's internal frontend network) is automatic and needs no command.
    """

    output = get_global_output_handler()
    services = ctx.obj["services"]

    confd_dir = Path(services.proxy_storage.dirs.confd.host)
    conf_path = confd_dir / _CONF_FILENAME

    if off and status:
        output.error("--off cannot be combined with --status", exception=typer.Exit(code=1))

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
            services.nginx_controller.reload()
            output.print("Removed real-ip configuration; proxy reloaded")
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
    conf_path.write_text(build_proxy_realip_conf(ranges, resolved_header, recursive=True))
    services.nginx_controller.reload()

    output.print(f"Real-ip active: trusting {len(ranges)} range(s), restoring client IP from {resolved_header}")
