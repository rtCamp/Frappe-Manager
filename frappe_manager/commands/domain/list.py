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

    Output is plain lines rather than a table, so a hostname can be copied out of it intact.
    """
    # Plain lines, not table cells: rich truncates or folds a long value, and both corrupt a
    # copied hostname. Same rule as `fm list --paths`.
    output = get_global_output_handler()
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
    sites = bench.bench_config.sites or {}
    # `site_names` falls back to the BENCH name when `[sites]` is empty, which is the legacy
    # fallback for benches created before names and sites came apart. A `--bench-only` bench
    # genuinely serves nothing, and that fallback made this command report the bench name as a
    # primary DOMAIN -- contradicting `fm info` and `fm list`, which both say it has no site.
    if not sites:
        empty = f"{bench.name} serves no site (created with --bench-only, or none recorded in bench_config.toml)"
        if json_result:
            output.print_data([])
        else:
            output.data_raw(empty)
        return

    site_names = bench.bench_config.site_names
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
