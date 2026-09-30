"""`fm services trusted-proxies`: which proxies in front of fm may speak for the client."""

import ipaddress
import re
from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example, install

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.modules.realip import (
    CLOUDFLARE_FALLBACK_RANGES,
    CLOUDFLARE_IPS_URLS,
    PROXY_CONF_FILENAME,
    build_proxy_realip_conf,
    is_fm_realip_conf,
    validate_cidrs,
)
from frappe_manager.utils.network import get_frontend_gateway

# nginx header names are tokens. Without this an `--client-ip-header 'X-Real-IP; deny all; #'`
# lands verbatim in `real_ip_header <value>;`, injecting arbitrary directives into the LIVE global
# proxy's conf.d.
_HEADER_TOKEN = re.compile(r"^[A-Za-z0-9-]+$")

trusted_proxies_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(trusted_proxies_app)


def _proxy_conf_is_valid(services) -> bool | None:
    """`nginx -t` inside the live global proxy. ``None`` when it is not running, so there is
    nothing to validate against and nothing to reload."""
    from frappe_manager.docker import DockerException

    if not services.is_service_running("nginx-proxy"):
        return None
    try:
        services.docker_client.compose.exec(service="nginx-proxy", command="nginx -t", stream=False)
    except DockerException:
        return False
    return True


def _restore_conf(conf_path: Path, previous: str | None) -> None:
    if previous is None:
        conf_path.unlink(missing_ok=True)
    else:
        conf_path.write_text(previous)


def _fetch_cloudflare_ranges(output) -> list[str]:
    """Live ranges from Cloudflare's published lists, vendored fallback when unreachable."""
    import requests

    ranges: list[str] = []
    try:
        for url in CLOUDFLARE_IPS_URLS:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            ranges += [line.strip() for line in response.text.splitlines() if line.strip()]
        if ranges:
            return ranges
    except Exception:
        output.warning("Could not fetch Cloudflare's published ranges; using the list vendored with fm")
    return list(CLOUDFLARE_FALLBACK_RANGES)


def _apply_downstream_trust(services, output) -> None:
    """Re-derive `TRUST_DOWNSTREAM_PROXY` and recreate the proxy if it moved.

    An environment variable is fixed at container creation, so unlike the conf.d files this one
    cannot be reloaded into a running proxy -- it is the only part of this command that costs an
    outage, and only when the trusted set crosses between empty and non-empty.
    """
    services.set_forwarded_trust_conf()
    if not services.apply_forwarded_trust_env():
        return
    services.compose_file_manager.write_to_file()
    if not services.is_service_running("nginx-proxy"):
        return
    output.print("Recreating the proxy to apply its forwarded-header trust")
    services.docker_client.compose.up(services=["nginx-proxy"], detach=True, force_recreate=True, stream=False)


@example(
    "See the directives actually in force",
    "",
    detail="Prints the rendered configuration, not the setting that produced it, which is what a trust problem needs.",
)
def show(ctx: typer.Context):
    """
    Show which proxies this host trusts and what is read from them.
    """
    output = get_global_output_handler()
    services = ctx.obj["services"]

    conf_path = Path(services.proxy_storage.dirs.confd.host) / PROXY_CONF_FILENAME

    if not (conf_path.exists() and is_fm_realip_conf(conf_path.read_text())):
        output.print("No proxies are trusted; fm treats every request as arriving directly.")
        return

    output.print(f"Trusted proxies ({conf_path}):")
    for line in conf_path.read_text().splitlines()[1:]:
        output.print(f"  {line}", emoji_code="")


@example(
    "Trust Cloudflare",
    "--cdn cloudflare",
    detail="Proxy logs, fm maintenance --allow-ip and frappe's rate limiting then see the visitor instead of Cloudflare's edge.",
)
@example(
    "Trust your own load balancer",
    "--trust 203.0.113.0/24",
    detail="Each run replaces the whole set, so pass every range you sit behind in one call.",
)
@example(
    "Trust a front running on the same machine",
    "--local",
    detail="Resolves the address a same-machine connection actually reaches fm from; it is never 127.0.0.1, because fm's proxy is a container and docker rewrites the source.",
)
def set_trusted(
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
    local: Annotated[
        bool,
        typer.Option("--local", help="Trust a reverse proxy running on this machine."),
    ] = False,
    client_ip_header: Annotated[
        str | None,
        typer.Option(
            "--client-ip-header",
            help="Header the client IP is read from. Defaults to CF-Connecting-IP for --cdn cloudflare and X-Forwarded-For otherwise; anything that is not a valid header name is refused.",
            show_default=False,
        ),
    ] = None,
):
    """
    Trust the proxies in front of fm, so the visitor's address and scheme survive the hop.

    Trust only the ranges you actually sit behind: whatever you trust fully controls the client IP and the scheme that fm, your logs and frappe go on to see. Anything arriving from any other address is judged on the connection itself, so a forged header changes nothing.

    Each run replaces the whole set.
    """
    output = get_global_output_handler()
    services = ctx.obj["services"]

    confd_dir = Path(services.proxy_storage.dirs.confd.host)
    conf_path = confd_dir / PROXY_CONF_FILENAME

    if client_ip_header is not None and not _HEADER_TOKEN.match(client_ip_header):
        # Rejected BEFORE anything is written: this value is rendered verbatim into a file that
        # is bind-mounted into the live global proxy's /etc/nginx/conf.d.
        output.error(
            f"--client-ip-header {client_ip_header!r} is not a valid header name (allowed: letters, digits and '-')",
            exception=typer.Exit(code=2),
        )

    if not cdn and not trust and not local:
        output.error(
            "Nothing to trust: pass --local, --cdn cloudflare and/or --trust CIDR (or use 'trusted-proxies clear')",
            exception=typer.Exit(code=2),
        )

    ranges: list[str] = []
    resolved_header = client_ip_header
    if cdn:
        if cdn.lower() != "cloudflare":
            output.error(
                f"Unsupported CDN {cdn!r}; supported: cloudflare (use --trust for custom ranges)",
                exception=typer.Exit(code=2),
            )
        ranges += _fetch_cloudflare_ranges(output)
        if resolved_header is None:
            resolved_header = "CF-Connecting-IP"
    if local:
        gateway = get_frontend_gateway(docker=services.docker_client)
        if not gateway:
            output.error(
                "Could not read the fm frontend network's gateway; is the global stack created?",
                exception=typer.Exit(code=1),
            )
        ranges.append(f"{gateway}/32")
        if resolved_header is None:
            resolved_header = "X-Forwarded-For"
    if trust:
        try:
            validated = validate_cidrs(trust)
        except ValueError as e:
            output.error(f"--trust: {e}", exception=typer.Exit(code=2))
        # A loopback range can never match: fm's proxy is a container, and docker source-NATs a
        # connection from this machine to the bridge gateway. Trusting it would look configured
        # and quietly trust nobody, which is the worst of the three possible outcomes.
        loopback = [cidr for cidr in validated if ipaddress.ip_network(cidr).is_loopback]
        if loopback:
            output.error(
                f"--trust {loopback[0]} can never match: fm's proxy runs in a container and never sees a connection from this machine as loopback. Use --local instead.",
                exception=typer.Exit(code=2),
            )
        ranges += validated
        if resolved_header is None:
            resolved_header = "X-Forwarded-For"

    if resolved_header is None:  # unreachable: cdn or trust is guaranteed above
        resolved_header = "X-Forwarded-For"

    conf_path.parent.mkdir(parents=True, exist_ok=True)
    previous = conf_path.read_text() if conf_path.exists() else None
    conf_path.write_text(build_proxy_realip_conf(ranges, resolved_header, recursive=True))
    services.set_forwarded_trust_conf()

    # The file lives in the global proxy's conf.d, so an nginx-invalid file does not just fail
    # here: the proxy refuses to start on its next restart, taking every bench on this host down
    # long after this command returned. Validate, and roll back rather than leave that behind.
    valid = _proxy_conf_is_valid(services)
    if valid is False:
        _restore_conf(conf_path, previous)
        services.set_forwarded_trust_conf()
        output.error(
            f"nginx rejected the configuration; {PROXY_CONF_FILENAME} was rolled back and the proxy left untouched",
            exception=typer.Exit(code=1),
        )

    summary = f"trusting {len(ranges)} range(s), reading the client IP from {resolved_header}"

    if valid is None:
        output.print(f"Trusted proxies written ({summary}); the global proxy is not running, so it applies on next start")
    else:
        _apply_downstream_trust(services, output)
        if services.nginx_controller.reload():
            output.print(f"Trusted proxies active: {summary}")
        else:
            output.warning(
                f"Trusted proxies written and validated ({summary}), but the proxy did not reload; "
                "run 'fm services restart nginx-proxy' to apply it"
            )

    _report_self_call_risk(ctx, output)
    _refresh_bench_web_servers(ctx, output)


@example(
    "Stop trusting anything in front",
    "",
    detail="Every request is then judged on the connection fm itself received, which is the right setting whenever nothing sits in front.",
)
def clear(ctx: typer.Context):
    """
    Trust nothing in front of fm.

    Use this whenever a front is removed: leaving its range trusted lets anyone reaching fm directly claim to be any client, over any scheme.
    """
    output = get_global_output_handler()
    services = ctx.obj["services"]

    conf_path = Path(services.proxy_storage.dirs.confd.host) / PROXY_CONF_FILENAME

    if not (conf_path.exists() and is_fm_realip_conf(conf_path.read_text())):
        output.print("No proxies were trusted")
        return

    conf_path.unlink()
    services.set_forwarded_trust_conf()
    _apply_downstream_trust(services, output)

    if services.nginx_controller.reload():
        output.print("No proxies are trusted any more; proxy reloaded")
    else:
        output.print("No proxies are trusted any more (the global proxy was not reloaded)")

    _refresh_bench_web_servers(ctx, output)


def _refresh_bench_web_servers(ctx, output) -> None:
    """Rewrite each bench's gunicorn wrapper from the new trusted set, and name what to restart.

    The wrapper carries whether gunicorn believes a forwarded scheme, which is a HOST fact, so the
    command that changes that fact owns the write. `fm restart` only re-execs a supervisor
    program's `command=` line from disk; nothing there can reach an already-running process, and
    nothing else rewrites this file. Without this a removed front leaves gunicorn trusting a
    header nobody is vouching for, indefinitely.

    One bench failing must not take the command down: the trust itself is already applied at the
    proxy, and a bench whose config will not load is a separate problem with its own message.
    """
    from frappe_manager import CLI_BENCHES_DIRECTORY
    from frappe_manager.site_manager.bench_config import FMBenchEnvType
    from frappe_manager.site_manager.site import Bench

    if not CLI_BENCHES_DIRECTORY.exists():
        return

    services = ctx.obj["services"]
    changed: list[str] = []
    for path in sorted(CLI_BENCHES_DIRECTORY.iterdir()):
        if not (path / "bench_config.toml").is_file():
            continue
        try:
            bench = Bench.get_object(path.name, services)
            changed_here = bench.supervisor.refresh_gunicorn_wrapper(bench.path)
            # Only a prod bench is told to restart: the wrapper is `command=` for the
            # `<bench>-frappe-web` supervisor program, and a dev bench runs `bench serve` from the
            # image's own frappe-dev.conf instead, so naming one sends the operator to restart
            # something the change cannot reach.
            if changed_here and bench.bench_config.environment_type == FMBenchEnvType.prod:
                changed.append(path.name)
        except Exception as e:
            output.warning(f"Could not update {path.name}'s web server configuration ({e})")

    if changed:
        output.print(
            f"Run 'fm restart {' '.join(changed)}' to apply this to the web server; "
            "the proxy change above does not reach an already-running gunicorn.",
            emoji_code="",
        )


def _report_self_call_risk(ctx, output) -> None:
    """Name the domains whose TLS is now the front's alone.

    A domain with no fm-side certificate is reachable over HTTPS through the front, but the site's
    own server-side calls to itself still land on fm's plain HTTP vhost -- which the redirect then
    bounces to https, to a listener holding no certificate. `fm ssl add --dev` is the fix, so the
    fact has to be said at the moment the trust is configured rather than found in a 503 later.

    Keyed on DOMAINS, not bench names: a certificate is filed under the domain it certifies, and a
    bench's name is not a domain (bench `redir` serves `redir.localhost`).
    """
    import os

    import tomlkit

    from frappe_manager import CLI_BENCHES_DIRECTORY

    services = ctx.obj["services"]
    certs_dir = Path(services.proxy_storage.dirs.certs.host)
    if not CLI_BENCHES_DIRECTORY.exists():
        return

    domains: list[str] = []
    for bench in sorted(CLI_BENCHES_DIRECTORY.iterdir()):
        config = bench / "bench_config.toml"
        if not config.is_file():
            continue
        try:
            # Parsed raw rather than through BenchConfig: this is an advisory line, and a bench
            # whose config fails validation must not take the command down with it.
            sites = tomlkit.parse(config.read_text()).get("sites") or {}
        except Exception:
            continue
        domains.extend(str(site) for site in sites)

    # `lexists`: the .crt is a symlink to a CONTAINER path, so `exists()` is False for every
    # certificate fm has ever issued (same trap as `certificate_linked` in services.py).
    affected = sorted(d for d in set(domains) if not os.path.lexists(certs_dir / f"{d}.crt"))
    if not affected:
        return

    output.print(
        f"TLS for {', '.join(affected)} is now your front's alone; fm holds no certificate for them, so their own server-side calls to themselves will fail. Run 'fm ssl add --dev <domain>' to give each one an internal certificate.",
        emoji_code="",
    )
