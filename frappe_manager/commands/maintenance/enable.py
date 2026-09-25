"""Maintenance enable command."""

import ipaddress
import os
import secrets
from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.maintenance._helpers import (
    _ALLOW_PATH_RE,
    _bench_domains,
    _extract_token,
    _page_filename,
    _resolve_page_html,
    _strip_fm_block,
    _vhost_conf,
    conf_state,
    proxy_paths,
)
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.callbacks import bench_site_autocompletion_callback, bench_site_callback


@example(
    "Put a bench into maintenance",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Put one site of a multi-site bench into maintenance",
    "{benchname}/shop.example.com",
    benchname="mybench",
)
@example(
    "Let the office and a payment webhook through",
    "{benchname} --allow-ip 203.0.113.7 --allow-path '/api/method/payment_webhook*'",
    benchname="mybench",
)
@example(
    "Say when you will be back",
    "{benchname} --message 'Back at 17:00 UTC' --retry-after 1800",
    benchname="mybench",
)
def enable(
    ctx: typer.Context,
    address: Annotated[
        str | None,
        typer.Argument(
            metavar="BENCH(/SITE)",
            help="Bench, or BENCH/SITE for one site's hostnames only.",
            autocompletion=bench_site_autocompletion_callback,
            callback=bench_site_callback,
        ),
    ] = None,
    response_code: Annotated[
        int,
        typer.Option(
            "--response-code",
            help="HTTP status code served while maintenance is on (400-599).",
        ),
    ] = 503,
    retry_after: Annotated[
        int,
        typer.Option(
            "--retry-after",
            help="Retry-After header in seconds; 0 omits it.",
        ),
    ] = 300,
    allow_ip: Annotated[
        list[str],
        typer.Option(
            "--allow-ip",
            help="Client IP that reaches the real site (repeatable; single addresses, no CIDR). Behind a CDN see fm services real-ip.",
            show_default=False,
        ),
    ] = [],
    allow_path: Annotated[
        list[str],
        typer.Option(
            "--allow-path",
            help="Request path served the real site, e.g. /api/method/ping (repeatable). Exact match; append * for a prefix.",
            show_default=False,
        ),
    ] = [],
    message: Annotated[
        str | None,
        typer.Option(
            "--message",
            help="Text shown on fm's built-in maintenance page.",
            show_default=False,
        ),
    ] = None,
    page: Annotated[
        Path | None,
        typer.Option(
            "--page",
            help="HTML file served as the page, instead of --message. A bench's configs/maintenance.html is used automatically.",
            show_default=False,
            # click's implicit readable=True would fail with its own wording before the hand
            # checks below (and _resolve_page_html's read) ever run; silenced so fm's own
            # messages are what an operator sees for both a missing and an unreadable file.
            readable=False,
        ),
    ] = None,
    rotate_token: Annotated[
        bool,
        typer.Option(
            "--rotate-token",
            help="Mint a fresh bypass token, invalidating every bypass URL and cookie already handed out.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Serve the maintenance page without asking for confirmation."),
    ] = False,
):
    """
    Put a bench's domains, aliases included, behind a maintenance page.

    fm maintenance enable BENCH covers every hostname the bench serves. fm maintenance enable BENCH/SITE covers that one site's own name and its aliases, leaving the bench's other sites serving: the page is written per domain in the shared proxy, so one site can be down while its neighbours are up.

    Enabling prints a secret bypass URL: open it once and a cookie lets you through to the real site for a day while everyone else gets the page (visit /fm-bypass/off to drop it sooner).

    Each enable rewrites the settings from the flags you pass, so repeat the ones you still want; only the bypass token carries over, unless you ask for --rotate-token.
    """

    output = get_global_output_handler()
    benchname = address

    # Same gate every other command that MUTATES a live bench carries (update, restart, start,
    # auth, reset...). Maintenance rewrites the bench's vhost in the shared proxy, so running it
    # against a bench whose on-disk layout predates the current fm is how you get a half-migrated
    # vhost. `stop` and `delete` are deliberately exempt: you must always be able to stop or remove
    # a bench you cannot migrate.
    check_bench_migration_required(benchname)

    if not 400 <= response_code <= 599:
        output.error(
            f"--response-code must be an HTTP error status between 400 and 599, got {response_code}",
            exception=typer.Exit(code=1),
        )

    if message is not None and page is not None:
        output.error("--message cannot be combined with --page (pick one)", exception=typer.Exit(code=1))

    if page is not None and not page.exists():
        output.error(f"--page file not found: {page}", exception=typer.Exit(code=1))

    if page is not None and not os.access(page, os.R_OK):
        output.error(f"--page file is not readable: {page}", exception=typer.Exit(code=1))

    for ip in allow_ip:
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            output.error(
                f"--allow-ip must be a single IPv4/IPv6 address, got {ip!r} (CIDR ranges are not supported here)",
                exception=typer.Exit(code=1),
            )

    for path in allow_path:
        if not _ALLOW_PATH_RE.match(path):
            output.error(
                f"--allow-path must be an absolute path like /api/method/ping (optionally ending in *), got {path!r}",
                exception=typer.Exit(code=1),
            )

    services, vhostd_dir, html_host_dir, html_container_dir = proxy_paths(ctx)

    # Narrowed to the named site when the address carries one.
    site = ctx.obj.get("site") if ctx.obj else None
    domains, domain_ssl, _all_domains = _bench_domains(benchname, site)

    # Serving a 503 to every hostname a bench owns is a K1 permission decision, and every other
    # command with that blast radius (prune, delete) asks first. Named domains, not a count: the
    # whole risk is taking down a hostname you did not realise this bench served. An already-live
    # maintenance page is not gated -- re-running to change the message or the allow lists adds no
    # exposure, and gating it would make an idempotent rerun prompt for nothing.
    if not yes and not all(conf_state(vhostd_dir / domain) for domain in domains):
        output.print(f"This will serve {response_code} to: {', '.join(domains)}")
        choice = output.prompt_ask(
            prompt="Put these domains into maintenance? (default: no)",
            choices=["yes", "no"],
            default="no",
            required_flag="--yes",
        )
        if choice != "yes":
            output.print("Aborted; nothing touched.", emoji_code="")
            raise typer.Exit(1)

    # Reuse the existing token when re-running (idempotent) unless a rotation was requested.
    token = None
    for domain in domains:
        path = vhostd_dir / domain
        if conf_state(path) and token is None:
            token = _extract_token(path.read_text())
    if rotate_token or token is None:
        token = secrets.token_hex(16)

    html_host_dir.mkdir(parents=True, exist_ok=True)
    (html_host_dir / _page_filename(benchname)).write_text(_resolve_page_html(benchname, page, message))
    vhostd_dir.mkdir(parents=True, exist_ok=True)
    for domain in domains:
        path = vhostd_dir / domain
        # The Secure flag on the bypass cookie is decided per domain: an alias
        # served over plain http must not be handed a cookie the browser will
        # only ever send back over TLS.
        block = _vhost_conf(
            benchname,
            token,
            html_container_dir,
            response_code,
            retry_after,
            allow_ip,
            allow_path,
            domain_ssl[domain],
        )
        # Prepend our block, preserving whatever else shares the file
        # (upload limits, hand-written directives).
        remainder = _strip_fm_block(path.read_text()).strip("\n") if path.exists() else ""
        path.write_text(block + (remainder + "\n" if remainder else ""))

    services.nginx_controller.reload()

    scheme = "https" if domain_ssl.get(domains[0]) else "http"
    output.print(f"Maintenance enabled for: {', '.join(domains)} (serving {response_code})")
    if allow_ip:
        output.print(f"Allowed IPs: {', '.join(allow_ip)}")
    if allow_path:
        output.print(f"Allowed paths: {', '.join(allow_path)}")
    output.print(f"Bypass (sets a cookie so you see the real site): {scheme}://{domains[0]}/fm-bypass/{token}")
    output.print(f"Drop the bypass again: {scheme}://{domains[0]}/fm-bypass/off")
