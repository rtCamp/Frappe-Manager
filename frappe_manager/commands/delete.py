from typing import Annotated, Optional
import typer
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.output_manager import get_global_output_handler


def delete(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompts")] = False,
    delete_db_from_global_db: Annotated[
        Optional[bool],
        typer.Option(
            "--delete-db-from-global-db/--no-delete-db-from-global-db",
            help="Delete database from global-db service",
        ),
    ] = None,
):
    """
    Delete a bench.

    Examples:

        fm delete mybench
        fm delete mybench --yes
        fm delete mybench --delete-db-from-global-db
    """

    if benchname:
        services_manager = ctx.obj["services"]
        verbose = ctx.obj['verbose']

        output = get_global_output_handler()
        bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
        bench_service.delete_bench(benchname, yes=yes, delete_db_from_global_db=delete_db_from_global_db)
