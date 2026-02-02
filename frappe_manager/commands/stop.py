from typing import Annotated, Optional
import typer
from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import spinner, get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager.commands import check_bench_migration_required


def stop(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
):
    """Stop a bench."""

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    with spinner(output, f"Stopping {benchname}"):
        bench.stop()
