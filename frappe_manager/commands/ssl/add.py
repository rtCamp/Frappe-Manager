"""Add SSL certificate command."""

from typing import Annotated

import typer

from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import temporary_stop
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.utils.callbacks import prompt_for_bench_selection, sites_autocompletion_callback

from .bench_helpers import _add_bench_certificate
from .external_helpers import _add_external_certificate
from .helpers import get_output_handler


def add_certificate(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench (omit for standalone mode).", autocompletion=sites_autocompletion_callback,
        ),
    ] = None,
    domain: Annotated[str | None, typer.Argument(help="Domain name for the certificate")] = None,
    challenge: Annotated[
        LETSENCRYPT_PREFERRED_CHALLENGE,
        typer.Option("--challenge", "-c", help="Challenge type"),
    ] = LETSENCRYPT_PREFERRED_CHALLENGE.http01,
    cname: Annotated[
        str | None,
        typer.Option("--cname", help="CNAME delegation record for DNS-01 challenge (requires dns01)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Test certificate generation using Let's Encrypt staging server without adding it to the system.",
        ),
    ] = False,
    standalone: Annotated[
        bool,
        typer.Option(
            "--standalone",
            help="Manage SSL for external (non-bench) Docker project. Use with docker network 'fm-global-frontend-network'.",
        ),
    ] = False,
    skip_dns_check: Annotated[
        bool,
        typer.Option(
            "--skip-dns-check",
            help="Skip DNS validation before certificate generation (use if DNS will be configured later).",
        ),
    ] = False,
    wait_for_dns: Annotated[
        bool,
        typer.Option(
            "--wait-for-dns",
            help="Wait for DNS propagation (polls every 30s for up to 5 minutes).",
        ),
    ] = False,
):
    """
    Add SSL certificate for a domain.

    Supports both bench mode (default) and standalone mode for external Docker projects.
    Standalone mode allows managing SSL for any Docker project using FM's nginx-proxy.

    Use --dry-run to test certificate generation with Let's Encrypt staging server
    before committing to production. This validates DNS/HTTP configuration without
    rate limits or system modifications.
    """

    if standalone:
        # Standalone mode: domain can be first arg (as benchname) or second arg
        actual_domain = domain if domain else benchname

        if not actual_domain:
            context = LoggerContext(operation="ssl-add-external")
            output = get_output_handler(ctx, context=context)
            output.display_error("Domain is required in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        if benchname and domain:
            context = LoggerContext(operation="ssl-add-external")
            output = get_output_handler(ctx, context=context)
            output.display_error("Cannot specify both benchname and domain in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _add_external_certificate(ctx, actual_domain, challenge, cname, dry_run, skip_dns_check, wait_for_dns)
    else:
        benchname = prompt_for_bench_selection(benchname)

        if not benchname or not domain:
            context = LoggerContext(operation="ssl-add")
            output = get_output_handler(ctx, context=context)
            output.display_error("Both benchname and domain are required in bench mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _add_bench_certificate(ctx, benchname, domain, challenge, cname, dry_run)
