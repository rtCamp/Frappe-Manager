import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument, JsonResultOption
from frappe_manager.output_manager import get_global_output_handler, railcard
from frappe_manager.site_manager.site import Bench


@example(
    "Show admin tools state for a bench",
    "{benchname}",
    benchname="mybench",
)
def status(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    json_result: JsonResultOption = False,
):
    """
    Report whether admin tools are configured, whether they are enabled, and which sites route to them.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(benchname)

    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    configured = bench.admin_tools.compose_file_manager.compose_path.exists()
    enabled = bool(bench.bench_config.admin_tools)

    if json_result:
        output.print_data(
            {
                "bench": bench.name,
                "configured": configured,
                "enabled": enabled,
                "sites": {
                    site_name: bench.bench_config.serves_admin_tools(site_name)
                    for site_name in bench.bench_config.site_names
                },
            }
        )
        return

    # Precondition for reachability, independent of any one site's route: the headline answers
    # "are the admin tools reachable" with the fact rows below carrying why.
    reachable = configured and enabled
    card = railcard.Card(bench.name, "reachable" if reachable else "not reachable", reachable)
    card.fact("containers", "configured" if configured else "not configured")
    card.fact("admin tools", "enabled" if enabled else "disabled")
    # Adminer and Mailpit are ONE container pair per bench; a site's row says whether its
    # nginx `location` block routes to them, not that the site has its own instance. Grouped
    # under one "sites" label (like bench_info's url rows) because a site DOMAIN is not a
    # short fact label, and the label column has no room for one.
    for i, site_name in enumerate(bench.bench_config.site_names):
        routed = bench.bench_config.serves_admin_tools(site_name)
        card.fact("sites" if i == 0 else "", f"{site_name}  {'routed' if routed else 'not routed'}")

    output.print_data(card.render())
