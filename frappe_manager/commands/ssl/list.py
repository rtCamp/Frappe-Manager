"""List SSL certificates command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.commands.arguments import StandaloneBenchNameArgument
from frappe_manager.output_manager import temporary_stop
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.utils.callbacks import prompt_for_bench_selection

from .bench_helpers import _list_bench_certificates
from .external_helpers import _list_external_certificates
from .helpers import get_output_handler


@example(
    "List a bench's certificates",
    "{benchname}",
    benchname="mybench",
)
@example(
    "List the external domains",
    "--standalone",
)
@example(
    "List every certificate fm manages",
    "--all",
)
def list_certificates(
    ctx: typer.Context,
    benchname: StandaloneBenchNameArgument = None,
    standalone: Annotated[
        bool,
        typer.Option("--standalone", help="List external (non-bench) domains instead of a bench."),
    ] = False,
    all: Annotated[
        bool,
        typer.Option("--all", help="List both external domains and every bench."),
    ] = False,
):
    """
    List SSL certificates with their expiry and renewal status.

    Lists one bench by default, including its domains that have no certificate yet. --standalone lists external Docker project domains instead, and --all lists both.

    The DNS Provider column names the \\[ssl.dns_providers] credential set each DNS-01 certificate authenticates with, "default" for the unlabelled account, and "(missing)" when the label or the default account is not stored at either scope.
    """

    if all:
        _list_all_certificates(ctx)
    elif standalone:
        _list_external_certificates(ctx)
    else:
        benchname = prompt_for_bench_selection(benchname)

        if not benchname:
            output = get_output_handler(ctx)
            output.display_error("Benchname required in bench mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _list_bench_certificates(ctx, benchname)


def _list_all_certificates(ctx: typer.Context):
    """List all SSL certificates (bench + external)."""

    services_manager = ctx.obj["services"]
    output = get_output_handler(ctx)

    output.print("\n[fm.accent]═══ External Certificates ═══[/fm.accent]\n", emoji_code="")
    _list_external_certificates(ctx)

    output.print("\n[fm.accent]═══ Bench Certificates ═══[/fm.accent]\n", emoji_code="")

    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager)
    benches = bench_service.get_bench_names()

    if not benches:
        output.print("No benches found", emoji_code=":information_source:")
    else:
        for bench_name in benches:
            output.print(f"\n[bold]Bench: {bench_name}[/bold]", emoji_code="")
            _list_bench_certificates(ctx, bench_name)
