"""Maintenance disable command."""

from pathlib import Path
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.maintenance._helpers import (
    _bench_domains,
    _extract_bench,
    _has_fm_block,
    _strip_fm_block,
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

    services, vhostd_dir, _html_host_dir, _html_container_dir = proxy_paths(ctx)

    site = ctx.obj.get("site") if ctx.obj else None
    domains, _domain_ssl, all_domains = _bench_domains(benchname, site)

    def strip(path: Path) -> None:
        # Remove ONLY the fm block; other directives in the shared
        # per-domain file (e.g. upload limits) must survive.
        remainder = _strip_fm_block(path.read_text()).strip("\n")
        if remainder:
            path.write_text(remainder + "\n")
        else:
            path.unlink()

    removed = 0
    for domain in domains:
        path = vhostd_dir / domain
        if not conf_state(path):
            continue
        strip(path)
        removed += 1

    # A domain dropped from the bench (`fm domain remove B/x`) keeps its vhost.d
    # file, and the loop above only knows the CURRENT bench_config.toml -- so its
    # maintenance block would stay live: still listed by status, and inherited (page and
    # bypass token) by whichever bench claims that domain next. Sweep those orphans, which
    # the block itself names as ours.
    orphans: list[str] = []
    if vhostd_dir.exists():
        for conf in sorted(vhostd_dir.iterdir()):
            # `all_domains`, not `domains`: with a site named, a sibling site's live block is
            # not an orphan, and disabling it would take a site the operator never mentioned
            # out of maintenance.
            if conf.name in all_domains or not conf.is_file():
                continue
            text = conf.read_text()
            if not _has_fm_block(text) or _extract_bench(text) != benchname:
                continue
            strip(conf)
            orphans.append(conf.name)
    removed += len(orphans)

    if not removed:
        output.print("Maintenance was not enabled")
        return
    services.nginx_controller.reload()
    output.print(f"Maintenance disabled for: {', '.join([*domains, *orphans])}")
