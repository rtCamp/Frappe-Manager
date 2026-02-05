"""Remove SSL certificate command."""

from typing import Annotated

import typer

from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import temporary_stop
from frappe_manager.utils.callbacks import prompt_for_bench_selection, sites_autocompletion_callback

from .bench_helpers import _remove_bench_certificate
from .external_helpers import _remove_external_certificate
from .helpers import get_output_handler


def remove_certificate(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench (omit for standalone mode).", autocompletion=sites_autocompletion_callback,
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
    Remove SSL certificate for a domain.

    Supports both bench mode (default) and standalone mode for external domains.
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
