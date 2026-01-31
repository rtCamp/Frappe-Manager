"""List SSL certificates command."""

from typing import Annotated, Optional
import typer
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import temporary_stop
from frappe_manager.utils.callbacks import sites_autocompletion_callback, prompt_for_bench_selection
from .helpers import get_output_handler
from .bench_helpers import _list_bench_certificates
from .external_helpers import _list_external_certificates


def list_certificates(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench (omit for standalone mode).", autocompletion=sites_autocompletion_callback
        ),
    ] = None,
    standalone: Annotated[
        bool,
        typer.Option("--standalone", help="List certificates for external (non-bench) domains"),
    ] = False,
    all: Annotated[
        bool,
        typer.Option("--all", help="List all certificates (bench + external)"),
    ] = False,
):
    """
    List SSL certificates.

    List certificates for a specific bench, external domains, or all certificates.
    """

    if all:
        _list_all_certificates(ctx)
    elif standalone:
        _list_external_certificates(ctx)
    else:
        benchname = prompt_for_bench_selection(benchname)

        if not benchname:
            context = LoggerContext(operation="ssl-list")
            output = get_output_handler(ctx, context=context)
            output.display_error("Benchname required in bench mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _list_bench_certificates(ctx, benchname)


def _list_all_certificates(ctx: typer.Context):
    """List all SSL certificates (bench + external)."""

    services_manager = ctx.obj["services"]
    context = LoggerContext(operation="ssl-list-all")
    output = get_output_handler(ctx, context=context)

    output.print("\n[bold cyan]═══ External Certificates ═══[/bold cyan]\n", emoji_code="")
    _list_external_certificates(ctx)

    output.print("\n[bold cyan]═══ Bench Certificates ═══[/bold cyan]\n", emoji_code="")

    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager)
    benches = bench_service.get_bench_names()

    if not benches:
        output.print("ℹ️  No benches found", emoji_code="")
    else:
        for bench_name in benches:
            output.print(f"\n[bold]Bench: {bench_name}[/bold]", emoji_code="")
            _list_bench_certificates(ctx, bench_name)
