"""Remove SSL certificate command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import StandaloneBenchNameArgument
from frappe_manager.output_manager import temporary_stop
from frappe_manager.utils.callbacks import prompt_for_bench_selection

from .bench_helpers import _remove_bench_certificate
from .external_helpers import _remove_external_certificate
from .helpers import get_output_handler


@example(
    "Delete a bench certificate",
    "{benchname} example.com",
    benchname="mybench",
)
@example(
    "Delete without the confirmation prompt",
    "{benchname} example.com --yes",
    benchname="mybench",
)
@example(
    "Delete an external domain's certificate",
    "example.com --standalone",
)
def remove_certificate(
    ctx: typer.Context,
    benchname: StandaloneBenchNameArgument = None,
    domain: Annotated[str | None, typer.Argument(help="Domain whose certificate to delete.")] = None,
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

    if standalone:
        # Standalone mode: domain can be first arg (as benchname) or second arg
        actual_domain = domain if domain else benchname

        if not actual_domain:
            output = get_output_handler(ctx)
            output.display_error("Domain is required in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _remove_external_certificate(ctx, actual_domain, yes)
    else:
        benchname = prompt_for_bench_selection(benchname)

        if not benchname or not domain:
            output = get_output_handler(ctx)
            output.display_error("Both benchname and domain are required in bench mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _remove_bench_certificate(ctx, benchname, domain, yes)
