from typing import Annotated, Optional, List
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import typer
from fmx.display import DisplayManager
from fmx.command_utils import validate_services, resolve_service_targets

from fmx.cli import ServiceNameEnumFactory, execute_parallel_command, get_service_names_for_completion
from fmx.supervisor.api import get_service_info as util_get_service_info
from fmx.supervisor.status_formatter import format_rq_status
from fmx.rq_controller import get_rq_worker_status

command_name = "status"

ServiceNamesEnum = ServiceNameEnumFactory()


def command(
    ctx: typer.Context,
    service_names: Annotated[
        Optional[List[ServiceNamesEnum]],
        typer.Argument(
            help="Name(s) of the service(s) to target. If omitted, targets ALL running services.",
            autocompletion=get_service_names_for_completion,
            show_default=False,
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show detailed process information.",
        ),
    ] = False,
):
    """Show detailed status of services and RQ workers."""
    display: DisplayManager = ctx.obj['display']

    all_services = get_service_names_for_completion()
    services_to_target = resolve_service_targets(service_names, all_services)

    valid, target_desc = validate_services(display, services_to_target, all_services, "check status")
    if not valid:
        return
    display.print(f"Fetching status for {target_desc}...")

    with ThreadPoolExecutor(max_workers=2) as executor:
        supervisor_future = executor.submit(
            execute_parallel_command,
            services_to_target,
            util_get_service_info,
            action_verb="checking status",
            show_progress=False,
            verbose=verbose,
        )
        rq_future = executor.submit(get_rq_worker_status)

        supervisor_error = None
        rq_status = None

        try:
            supervisor_future.result(timeout=10)
        except Exception as e:
            supervisor_error = e
            display.warning(f"Failed to fetch supervisor status: {e}")

        try:
            rq_status = rq_future.result(timeout=5)
        except FuturesTimeoutError:
            display.warning("⚠ RQ worker status fetch timed out (5s)")
        except Exception as e:
            display.warning(f"⚠ Could not fetch RQ worker status: {e}")

    if supervisor_error:
        display.print("Status check completed with errors.")
        return

    if rq_status:
        rq_tree = format_rq_status(rq_status, verbose=verbose)
        if rq_tree:
            display.print(rq_tree)
    else:
        display.warning("⚠ RQ worker status unavailable")

    display.print("Status check finished.")
