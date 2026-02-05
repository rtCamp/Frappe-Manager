from typing import Annotated

import typer

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    sitename_callback,
    sites_autocompletion_callback,
)


def logs(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ] = None,
    service: Annotated[str | None, typer.Option(help="Service name (frappe, nginx, redis-cache, etc.)")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs in real-time")] = False,
):
    """Show bench logs (server or container)"""

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    if service:
        available_services = bench.get_available_services()
        if service not in available_services:
            output.display_error(f"Service '{service}' not found")
            output.print(f"Available services: {', '.join(sorted(available_services))}")
            raise typer.Exit(1)

    bench.logs(follow, service)
