from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    sitename_callback,
    sites_autocompletion_callback,
)


@example(
    "Show frappe server logs",
    "{benchname}",
    detail="Displays the frappe service logs for the bench; useful to inspect server output and errors.",
    benchname="mybench",
)
@example(
    "Follow logs in real-time",
    "{benchname} -f",
    detail="Streams logs continuously; press Ctrl+C to stop following.",
    benchname="mybench",
)
@example(
    "Show nginx container logs",
    "{benchname} --service nginx -f",
    detail="Shows the nginx container logs for the bench and follows them in real-time when -f is provided.",
    benchname="mybench",
)
@example(
    "Show redis logs",
    "{benchname} --service redis-cache",
    detail="Displays logs from redis-cache service; helpful when debugging caching or queuing issues.",
    benchname="mybench",
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
    """
    Show bench logs (server or container).

    View logs from bench services (frappe, nginx, redis, etc.) with optional follow mode.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if service:
        available_services = bench.get_available_services()
        if service not in available_services:
            output.display_error(f"Service '{service}' not found")
            output.print(f"Available services: {', '.join(sorted(available_services))}")
            raise typer.Exit(1)

    bench.logs(follow, service)
