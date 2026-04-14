from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback


@example(
    "Stop bench containers",
    "{benchname}",
    detail="Stops all running containers for the specified bench without removing any data. Use to shut down a bench safely.",
    benchname="mybench",
)
@example(
    "Stop multiple benches",
    "mybench && fm stop another-bench",
    detail="Chain multiple stop commands to shut down several benches at once.",
)
def stop(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ] = None,
):
    """
    Stop a bench.

    Stops all containers for the given bench. No data is removed; containers can be started again with 'fm start'.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    with spinner(output, f"Stopping {benchname}"):
        bench.stop()
