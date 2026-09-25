"""Maintenance status command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.maintenance._helpers import (
    _bench_domains,
    _extract_bench,
    _extract_code,
    _extract_token,
    _has_fm_block,
    conf_state,
    optional_bench_site_callback,
    proxy_paths,
)
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.callbacks import bench_site_autocompletion_callback


@example(
    "Check one bench",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Check one site's hostnames only",
    "{benchname}/shop.example.com",
    benchname="mybench",
)
@example(
    "See every domain in maintenance, across every bench",
    "",
)
def status(
    ctx: typer.Context,
    address: Annotated[
        str | None,
        typer.Argument(
            metavar="BENCH(/SITE)",
            help="Bench, or BENCH/SITE for one site's hostnames only. Omit it to list every domain in maintenance, across every bench.",
            autocompletion=bench_site_autocompletion_callback,
            callback=optional_bench_site_callback,
        ),
    ] = None,
):
    """
    Report maintenance state per domain, with the bypass URL.

    Writes nothing. Without a bench, lists every domain currently in maintenance across every bench.
    """

    output = get_global_output_handler()
    benchname = address
    _services, vhostd_dir, _html_host_dir, _html_container_dir = proxy_paths(ctx)

    if benchname is None:
        found = 0
        if vhostd_dir.exists():
            for conf in sorted(vhostd_dir.iterdir()):
                if not conf.is_file():
                    continue
                text = conf.read_text()
                if not _has_fm_block(text):
                    continue
                found += 1
                output.print(
                    f"{conf.name}: maintenance ON (bench {_extract_bench(text)}, code {_extract_code(text)}, "
                    f"bypass token {_extract_token(text)})"
                )
        if not found:
            output.print("No domain is in maintenance")
        return

    check_bench_migration_required(benchname)

    site = ctx.obj.get("site") if ctx.obj else None
    domains, domain_ssl, _all_domains = _bench_domains(benchname, site)

    for domain in domains:
        path = vhostd_dir / domain
        scheme = "https" if domain_ssl.get(domain) else "http"
        if conf_state(path):
            text = path.read_text()
            output.print(
                f"{domain}: maintenance ON "
                f"(code {_extract_code(text)}, bypass: {scheme}://{domain}/fm-bypass/{_extract_token(text)})"
            )
        elif path.exists():
            output.print(f"{domain}: custom vhost config present (no fm maintenance block)")
        else:
            output.print(f"{domain}: maintenance off")
