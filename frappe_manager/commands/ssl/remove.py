"""Remove SSL certificate command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import temporary_stop
from frappe_manager.utils.callbacks import prompt_for_bench_selection, sites_autocompletion_callback

from .bench_helpers import _remove_bench_certificate
from .external_helpers import _remove_external_certificate
from .helpers import get_output_handler


@example(
    "Remove SSL certificate from a bench",
    "{benchname} example.com",
    detail="Removes the certificate from the bench and its nginx configuration. Use --yes to skip confirmation.",
    benchname="mybench",
)
@example(
    "Remove without confirmation",
    "{benchname} example.com --yes",
    detail="Removes the certificate immediately without prompting for confirmation.",
    benchname="mybench",
)
@example(
    "Remove external (standalone) certificate",
    "example.com --standalone",
    detail="Removes a certificate managed in standalone mode for external Docker projects.",
)
def remove_certificate(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench (omit for standalone mode).",
            autocompletion=sites_autocompletion_callback,
        ),
    ] = None,
    domain: Annotated[str | None, typer.Argument(help="Domain name of the certificate to remove")] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt"),
    ] = False,
    standalone: Annotated[
        bool,
        typer.Option("--standalone", help="Remove certificate for external (non-bench) domain"),
    ] = False,
):
    """
    Remove an SSL certificate for a domain.

    Works in bench mode (removes from a bench) or standalone mode for external Docker projects.
    """

    if standalone:
        # Standalone mode: domain can be first arg (as benchname) or second arg
        actual_domain = domain if domain else benchname

        if not actual_domain:
            context = LoggerContext(operation="ssl-remove-external")
            output = get_output_handler(ctx, context=context)
            output.display_error("Domain is required in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _remove_external_certificate(ctx, actual_domain, yes)
    else:
        benchname = prompt_for_bench_selection(benchname)

        if not benchname or not domain:
            context = LoggerContext(operation="ssl-remove")
            output = get_output_handler(ctx, context=context)
            output.display_error("Both benchname and domain are required in bench mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _remove_bench_certificate(ctx, benchname, domain, yes)
