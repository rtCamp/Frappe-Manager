"""Disable admin tools command."""

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchSiteAllArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench

from ._helpers import route_sites


@example(
    "Stop the admin tools containers for a bench",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Unroute one site, leaving the containers running for the rest",
    "{benchname}/site1.localhost",
    benchname="mybench",
)
@example(
    "Unroute every site the bench serves",
    "{benchname}/all",
    detail="The Adminer and Mailpit containers keep running; stop them with a bare 'fm tools disable BENCH'.",
    benchname="mybench",
)
def disable(
    ctx: typer.Context,
    address: BenchSiteAllArgument = None,
):
    """
    Stop the admin tools (Adminer at /adminer, Mailpit at /mailpit), or unroute a site from them.

    BENCH stops the one container pair the bench has. BENCH/SITE only drops the routes for that site's hostnames, leaving the tools running for the bench's other sites; BENCH/all drops every site's route without stopping the containers.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(address)

    # The site half of the address, put there by `bench_site_all_callback`.
    site = ctx.obj.get("site") if ctx.obj else None

    bench = Bench.get_object(address, services_manager, output_handler=output)

    if site:
        route_sites(bench, site, output, wanted=False)
        return

    if not bench.admin_tools.compose_file_manager.compose_path.exists() or not bench.bench_config.admin_tools:
        # Report and stop, rather than raising: an already-disabled bench is not an error, it is
        # this command's own goal state already reached.
        output.print("Admin tools is already disabled")
        return

    with spinner(output, "Disabling admin tools"):
        bench.bench_config.admin_tools = False
        bench.admin_tools.disable()

    bench.save_bench_config()
    output.print("Disabled Admin-tools")
