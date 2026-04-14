import typer
from typer_examples import example

from frappe_manager import (
    CLI_BENCHES_DIRECTORY,
)
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_service import BenchService


@example(
    "List all available benches",
    "",
    detail="Shows a table of all benches managed by FM with status and environment information.",
)
def list(ctx: typer.Context):
    """
    List all benches.

    Displays a table showing all available benches with their status, environment type,
    and other relevant information. Use this to see what benches exist on your system.
    """

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
    table = bench_service.list_benches_table()
    if table.row_count:
        output.print_data(table)
