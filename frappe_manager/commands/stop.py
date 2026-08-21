import typer
from typer_examples import example

from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench


@example(
    "Stop a bench",
    "{benchname}",
    benchname="mybench",
)
def stop(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
):
    """
    Stop a bench's containers, admin tools and workers.

    Nothing is deleted; fm start brings the bench back.
    """
    # No migration gate here: "stop" is in app_callback's commands_skip_bench_migration
    # whitelist (with "delete"), i.e. stopping an unmigrated bench must always work.

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    with spinner(output, f"Stopping {benchname}"):
        bench.stop()
