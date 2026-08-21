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
    "Issue a certificate for a bench domain",
    "{benchname} example.com",
    benchname="mybench",
)
@example(
    "Issue one when the domain has no public A record",
    "{benchname} example.com --challenge dns01",
    detail="DNS-01 needs provider credentials, see fm ssl dns-config.",
    benchname="mybench",
)
@example(
    "Rehearse against the staging server first",
    "{benchname} example.com --dry-run",
    benchname="mybench",
)
@example(
    "Issue for an external Docker project",
    "example.com --standalone",
)
@example(
    "Validate through a delegated zone",
    "{benchname} example.com --challenge dns01 --cname acme.example.net",
    detail="acme.sh looks for _acme-challenge.acme.example.net instead of the bench's own zone.",
    benchname="mybench",
)
def add_certificate(
    ctx: typer.Context,
    benchname: StandaloneBenchNameArgument = None,
    domain: Annotated[str | None, typer.Argument(help="Domain to issue the certificate for.")] = None,
    challenge: Annotated[
        LETSENCRYPT_PREFERRED_CHALLENGE,
        typer.Option("--challenge", "-c", help="ACME validation method."),
    ] = LETSENCRYPT_PREFERRED_CHALLENGE.http01,
    cname: Annotated[
        str | None,
        typer.Option("--cname", help="Delegated zone for _acme-challenge. dns01 only."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Rehearse against Let's Encrypt staging. Nothing is kept: no certificate, no nginx change.",
        ),
    ] = False,
    standalone: Annotated[
        bool,
        typer.Option(
            "--standalone",
            help="For an external Docker project on the fm-global-frontend-network network.",
        ),
    ] = False,
    dev: Annotated[
        bool,
        typer.Option(
            "--dev",
            help="Issue from fm's local CA, so no internet or public DNS is needed. Bench mode only.",
        ),
    ] = False,
    skip_dns_check: Annotated[
        bool,
        typer.Option(
            "--skip-dns-check",
            help="Skip the DNS pre-check. Standalone mode only.",
        ),
    ] = False,
    wait_for_dns: Annotated[
        bool,
        typer.Option(
            "--wait-for-dns",
            help="Wait up to 5 min for the CNAME. Standalone only.",
        ),
    ] = False,
):
    """
    Issue an SSL certificate for a domain and point nginx at it.

    Bench mode takes a bench name and one of its configured domains (add new ones with fm update --add-alias). --standalone issues for an external Docker project instead.
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
