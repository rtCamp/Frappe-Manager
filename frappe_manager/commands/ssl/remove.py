"""Remove SSL certificate command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import BenchDomainArgument
from frappe_manager.output_manager import temporary_stop
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME, prompt_for_bench_selection

from .bench_helpers import _remove_bench_certificate, _resolve_domains
from .external_helpers import _remove_external_certificate
from .helpers import get_output_handler


@example(
    "Delete a bench certificate",
    "{benchname}/example.com",
    benchname="mybench",
)
@example(
    "Delete without the confirmation prompt",
    "{benchname}/example.com --yes",
    benchname="mybench",
)
@example(
    "Delete every certificate the bench holds",
    "{benchname}/all",
    detail="Back to plain HTTP on every domain of that bench. Bare 'all' is refused here.",
    benchname="mybench",
)
@example(
    "Delete an external domain's certificate",
    "example.com --standalone",
)
def remove_certificate(
    ctx: typer.Context,
    address: BenchDomainArgument = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Delete without asking for confirmation."),
    ] = False,
    standalone: Annotated[
        bool,
        typer.Option("--standalone", help="Target an external (non-bench) domain."),
    ] = False,
):
    """
    Delete an SSL certificate and go back to serving the domain over plain HTTP.

    Asks for confirmation unless you pass --yes. --standalone deletes an external Docker project's certificate and nginx config instead of a bench's.
    """

    # The address's second segment, put there by `bench_domain_callback`.
    domain = ctx.obj.get("domain") if ctx.obj else None

    if address == RESERVED_BENCH_NAME:
        output = get_output_handler(ctx)
        output.display_error(
            "'all' is not accepted here: deleting every certificate of every bench would take every "
            "domain fm serves back to plain HTTP in one command. Name a bench, and use 'BENCH/all' "
            "for every certificate of that one."
        )
        raise typer.Exit(1)

    if standalone:
        if domain:
            output = get_output_handler(ctx)
            output.display_error(
                "An external domain belongs to no bench, so --standalone takes a bare domain: "
                f"use 'fm ssl remove {domain} --standalone'."
            )
            raise typer.Exit(1)

        if not address:
            output = get_output_handler(ctx)
            output.display_error("Domain is required in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _remove_external_certificate(ctx, address, yes)
        return

    address = prompt_for_bench_selection(address)

    if not address or not domain:
        output = get_output_handler(ctx)
        output.display_error(
            "An address of the form BENCH/DOMAIN is required in bench mode, naming the certificate "
            "to delete. 'BENCH/all' deletes every certificate the bench holds."
        )
        with temporary_stop(output):
            typer.echo(ctx.get_help())
        raise typer.Exit(1)

    for target in _resolve_domains(ctx, address, domain):
        _remove_bench_certificate(ctx, address, target, yes)
