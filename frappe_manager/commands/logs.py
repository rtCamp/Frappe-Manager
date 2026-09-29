from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench


@example(
    "Read the bench's web server log",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Follow it live",
    "{benchname} -f",
    benchname="mybench",
)
@example(
    "Read a container's logs instead",
    "{benchname} --service nginx -f",
    benchname="mybench",
)
def logs(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    service: Annotated[
        str | None,
        typer.Option(help="Compose service whose container logs to show (frappe, nginx, redis-cache, ...)."),
    ] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Keep streaming new lines until Ctrl+C.")] = False,
):
    """
    Show a bench's web server log, or a container's log with --service.

    Without --service this reads the bench's log files on the host, so it works whether or not the bench is up. With --service the logs come from docker and that container has to be running.
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

    # The host log files outlive the containers, which is the point (see the docstring), but with
    # nothing said a stopped bench's last-run output reads as live: the audit found `fm logs` on a
    # stopped bench printing 105 lines and exiting 0 with no hint the bench was down. `--follow`
    # is worse, since it waits on a file nothing will append to.
    if not service and not bench.running:
        tail = "; --follow will wait on a file nothing is writing to" if follow else ""
        output.warning(f"{bench.name} is not running, so this is the last run's log{tail}. Start it with 'fm start {bench.name}'.")

    bench.logs(follow, service)
