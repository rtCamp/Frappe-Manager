from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_service import BenchService


@example(
    "Delete a bench and its database",
    "{benchname} --delete-db-from-global-db",
    benchname="mybench",
)
@example(
    "Delete the bench but keep the database",
    "{benchname} --no-delete-db-from-global-db",
    detail="The bench is gone; the schema stays in global-db.",
    benchname="mybench",
)
@example(
    "Delete unattended",
    "{benchname} --yes --delete-db-from-global-db",
    benchname="mybench",
)
def delete(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", "-y", help="Delete without the removal confirmation. The database question is asked anyway."
        ),
    ] = False,
    delete_db_from_global_db: Annotated[
        bool | None,
        typer.Option(
            "--delete-db-from-global-db/--no-delete-db-from-global-db",
            help="Drop the site's schema and user from the global-db container, or keep them. Never touches a database on an external server. fm asks when neither is passed.",
        ),
    ] = None,
):
    """
    Delete a bench: its containers and volumes, its whole directory, and its TLS certificate.

    The database is decided separately. fm can drop the site's schema and user from the global-db container it owns, but a schema on a server fm does not own is always left in place, --delete-db-from-global-db or not.
    """

    if benchname:
        services_manager = ctx.obj["services"]
        verbose = ctx.obj["verbose"]

        output = get_global_output_handler()
        bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
        bench_service.delete_bench(benchname, yes=yes, delete_db_from_global_db=delete_db_from_global_db)
