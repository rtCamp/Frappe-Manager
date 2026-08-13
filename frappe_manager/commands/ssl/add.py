"""Add SSL certificate command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import StandaloneBenchNameArgument
from frappe_manager.output_manager import temporary_stop
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.utils.callbacks import prompt_for_bench_selection

from .bench_helpers import _add_bench_certificate
from .external_helpers import _add_external_certificate
from .helpers import get_output_handler


@example(
    "Add SSL certificate with HTTP-01 challenge",
    "{benchname} example.com --challenge http01",
    detail="Requests a certificate using an HTTP-01 challenge and installs it into the bench's nginx configuration.",
    benchname="mybench",
)
@example(
    "Add SSL certificate with DNS-01 challenge (Cloudflare)",
    "{benchname} example.com --challenge dns01",
    detail="Requests a certificate using DNS-01 validation; configure DNS provider credentials first when required.",
    benchname="mybench",
)
@example(
    "Add for external Docker project (standalone mode)",
    "example.com --standalone",
    detail="Manage SSL for an external Docker project by using standalone mode and FM's nginx-proxy.",
)
@example(
    "Test with Let's Encrypt staging (dry-run)",
    "{benchname} example.com --dry-run",
    detail="Performs a dry-run against Let's Encrypt staging environment to validate configuration without issuing production certs.",
    benchname="mybench",
)
@example(
    "Add with CNAME delegation for DNS validation",
    "{benchname} example.com --challenge dns01 --cname delegated.example.com",
    detail="Uses a CNAME delegation target for DNS-01 validation when the DNS zone is delegated to another provider.",
    benchname="mybench",
)
def add_certificate(
    ctx: typer.Context,
    benchname: StandaloneBenchNameArgument = None,
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
    dev: Annotated[
        bool,
        typer.Option(
            "--dev",
            help="Issue a locally-trusted dev certificate using a local CA (no internet required).",
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
    Add an SSL certificate for a domain.

    Supports bench mode (adds certificate to a bench) and standalone mode for external Docker projects using FM nginx-proxy. Use --dry-run to validate issuance against Let's Encrypt staging first.
    """

    if dev and standalone:
        output = get_output_handler(ctx)
        output.display_error("--dev cannot be used with --standalone mode")
        raise typer.Exit(1)

    if standalone:
        # Standalone mode: domain can be first arg (as benchname) or second arg
        actual_domain = domain if domain else benchname

        if not actual_domain:
            output = get_output_handler(ctx)
            output.display_error("Domain is required in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        if benchname and domain:
            output = get_output_handler(ctx)
            output.display_error("Cannot specify both benchname and domain in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _add_external_certificate(ctx, actual_domain, challenge, cname, dry_run, skip_dns_check, wait_for_dns)
    else:
        benchname = prompt_for_bench_selection(benchname)

        if not benchname or not domain:
            output = get_output_handler(ctx)
            output.display_error("Both benchname and domain are required in bench mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _add_bench_certificate(ctx, benchname, domain, challenge, cname, dry_run, dev=dev)
