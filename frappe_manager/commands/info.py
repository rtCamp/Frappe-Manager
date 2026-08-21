import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench


@example(
    "Show everything about a bench",
    "{benchname}",
    benchname="mybench",
)
def info(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
):
    """
    Show a bench's URL, credentials, apps, deploy history and live service state.

    The administrator and database passwords are printed in cleartext.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    with spinner(output, "Getting bench info"):
        bench.info()
