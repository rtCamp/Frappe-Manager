"""Admin tools status command."""

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench


@example(
    "Show admin tools state for a bench",
    "{benchname}",
    benchname="mybench",
)
def status(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
):
    """
    Report whether admin tools are configured, whether they are enabled, and which sites route to them.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(benchname)

    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    configured = bench.admin_tools.compose_file_manager.compose_path.exists()
    enabled = bench.bench_config.admin_tools

    lines = [
        f"containers: {'configured' if configured else 'not configured'}",
        f"admin tools: {'enabled' if enabled else 'disabled'}",
    ]
    for site_name in bench.bench_config.site_names:
        routed = bench.bench_config.serves_admin_tools(site_name)
        lines.append(f"{site_name}: {'routed' if routed else 'not routed'}")

    for line in lines:
        output.data_raw(line)
