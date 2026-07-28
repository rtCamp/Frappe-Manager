from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    sitename_callback,
    sites_autocompletion_callback,
)


@example(
    "Show bench details and configuration",
    "{benchname}",
    detail="Displays bench status, environment type, apps installed, and other configuration details useful for debugging and documentation.",
    benchname="mybench",
)
def info(
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
    Show bench information and configuration.

    Displays bench status, installed apps, environments, and other relevant configuration.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    with spinner(output, "Getting bench info"):
        bench.info()
