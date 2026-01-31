from typing import Annotated, Optional
import typer
from frappe_manager.logger.context import LoggerContext
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.commands import get_output_handler


def delete(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation prompts")] = False,
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
        fm delete mybench --force
        fm delete mybench --delete-db-from-global-db
    """

    if benchname:
        services_manager = ctx.obj["services"]
        verbose = ctx.obj['verbose']

        # Create context for this operation
        context = LoggerContext(bench=benchname, operation="delete")
        output = get_output_handler(ctx, context=context)
        bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
        bench_service.delete_bench(benchname, force=force, delete_db_from_global_db=delete_db_from_global_db)
