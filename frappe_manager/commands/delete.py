from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_service import BenchService


@example(
    "Delete a bench",
    "{benchname}",
    detail="Deletes the bench directory and associated containers. This is destructive and will remove local bench data.",
    benchname="mybench",
)
@example(
    "Delete without confirmation",
    "{benchname} --yes",
    detail="Performs deletion without interactive confirmation. Use with caution in scripts or automation.",
    benchname="mybench",
)
@example(
    "Delete bench and its database from global-db",
    "{benchname} --delete-db-from-global-db",
    detail="Also deletes the bench's database from the global-db service. This permanently removes stored site data.",
    benchname="mybench",
)
def delete(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompts")] = False,
    delete_db_from_global_db: Annotated[
        bool | None,
        typer.Option(
            "--delete-db-from-global-db/--no-delete-db-from-global-db",
            help="Delete database from global-db service",
        ),
    ] = None,
):
    """
    Delete a bench and optionally its database from global-db service.

    Removes the bench directory, containers, and associated data. Use --yes to avoid confirmation prompts.
    """

    if benchname:
        services_manager = ctx.obj["services"]
        verbose = ctx.obj["verbose"]

        output = get_global_output_handler()
        bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
        bench_service.delete_bench(benchname, yes=yes, delete_db_from_global_db=delete_db_from_global_db)
