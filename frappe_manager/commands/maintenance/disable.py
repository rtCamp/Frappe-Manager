from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.maintenance._helpers import (
    _bench_domains,
    _extract_bench,
    _maintenance_domains,
    conf_state,
    proxy_paths,
)
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.callbacks import bench_site_autocompletion_callback, bench_site_callback


@example(
    "Bring the bench back",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Bring one site back, leaving the bench's others in maintenance",
    "{benchname}/shop.example.com",
    benchname="mybench",
)
def disable(
    ctx: typer.Context,
    address: Annotated[
        str | None,
        typer.Argument(
            metavar="BENCH(/SITE)",
            help="Bench, or BENCH/SITE for one site's hostnames only. A bare bench name covers every domain it serves.",
            autocompletion=bench_site_autocompletion_callback,
            callback=bench_site_callback,
        ),
    ] = None,
):
    """
    Take the addressed domains out of maintenance and serve them again.

    A bare bench name covers every domain it serves; BENCH/SITE covers that one site's own name and its aliases, leaving the bench's other sites as they are.
    """

    output = get_global_output_handler()
    benchname = address

    check_bench_migration_required(benchname)

    services, dropins, _html_host_dir, _html_container_dir = proxy_paths(ctx)

    site = ctx.obj.get("site") if ctx.obj else None
    domains, _domain_ssl, all_domains = _bench_domains(benchname, site)

    removed = 0
    for domain in domains:
        if not conf_state(dropins, domain):
            continue
        dropins.remove(domain, "maintenance")
        removed += 1

    # A domain dropped from the bench (`fm domain remove B/x`) keeps its fragment, and the loop
    # above only knows the CURRENT bench_config.toml -- so it would stay live: still listed by
    # status, and inherited (page and bypass token) by whichever bench claims that domain next.
    # Sweep those orphans, which the fragment itself names as ours.
    orphans: list[str] = []
    for domain in _maintenance_domains(dropins):
        # `all_domains`, not `domains`: with a site named, a sibling site's live fragment is not
        # an orphan, and disabling it would take a site the operator never mentioned out of
        # maintenance.
        if domain in all_domains:
            continue
        if _extract_bench(dropins.fragment_path(domain, "maintenance").read_text()) != benchname:
            continue
        dropins.remove(domain, "maintenance")
        orphans.append(domain)
    removed += len(orphans)

    if not removed:
        output.print("Maintenance was not enabled")
        return
    services.nginx_controller.reload()
    output.print(f"Maintenance disabled for: {', '.join([*domains, *orphans])}")
