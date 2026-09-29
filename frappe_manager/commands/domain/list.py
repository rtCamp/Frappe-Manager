import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument, JsonResultOption
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench


@example(
    "List a bench's domains",
    "{benchname}",
    benchname="mybench",
)
def list_domains(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    json_result: JsonResultOption = False,
):
    """
    List every site's primary domain and its alias domains, one per line.

    Copy targets get PLAIN lines, not table cells (commands/list.py:61): a rich cell would truncate or fold a long hostname, corrupting anything copied out of it.
    """
    output = get_global_output_handler()
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
    site_names = bench.bench_config.site_names
    sites = bench.bench_config.sites or {}
    primary = bench.bench_config.primary_site_or_none()
    width = max((len(name) for name in site_names), default=0)

    if json_result:
        output.print_data(
            [
                {
                    "site": site_name,
                    "primary": site_name == primary,
                    "aliases": (sites[site_name].alias_domains if site_name in sites else []) or [],
                }
                for site_name in site_names
            ]
        )
        return

    for site_name in site_names:
        role = "primary" if site_name == primary else "site"
        output.data_raw(f"{site_name:<{width}}  {role}")
        entry = sites.get(site_name)
        for alias in (entry.alias_domains if entry else []) or []:
            output.data_raw(f"{site_name:<{width}}  {alias}")
