from typing import Annotated, Optional
import typer
from frappe_manager.output_manager import spinner, get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager.commands import check_bench_migration_required


def reset(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    admin_pass: Annotated[
        Optional[str],
        typer.Option(help="Password for the 'Administrator' User."),
    ] = None,
):
    """Drop database and reinstall all apps"""

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]

    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    with spinner(output, f"Resetting {benchname}"):
        bench.reset(admin_pass)
