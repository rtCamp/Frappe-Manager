import sys
from typing import Annotated, Optional, List

import typer
from ..display import DisplayManager
from ..cli import (
    ServiceNameEnumFactory,
    execute_parallel_command,
    get_service_names_for_completion,
    _cached_service_names,
)
from ..supervisor import stop_service as util_stop_service, FM_SUPERVISOR_SOCKETS_DIR

command_name = "stop"

ServiceNamesEnum = ServiceNameEnumFactory()

def command(
    ctx: typer.Context,
    service_names: Annotated[
        Optional[List[ServiceNamesEnum]],
        typer.Argument(
            help="Name(s) of the service(s) to target. If omitted, targets ALL running services.",
            autocompletion=get_service_names_for_completion,
            show_default=False,
        )
    ] = None,
    process_name: Annotated[
        Optional[List[str]],
        typer.Option(
            "--process", "-p",
            help="Target only specific process(es) within the selected service(s). Use multiple times for multiple processes (e.g., -p worker_short -p worker_long).",
            show_default=False,
        )
    ] = None,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Wait for supervisor start/stop operations to complete before returning.",
        )
    ] = True,
    wait_workers: Annotated[
        Optional[bool],
        typer.Option(
            "--wait-workers/--no-wait-workers",
            help="Wait for processes identified as workers to stop gracefully (use if default stop times out workers).",
            show_default=False,
        )
    ] = None,
):
    """Stop services or specific processes."""
    # Get display manager from context dictionary
    display: DisplayManager = ctx.obj['display']

    if not _cached_service_names:
        display.error(f"No supervisord services found to stop.", exit_code=1)
        display.print(f"Looked for socket files in: {FM_SUPERVISOR_SOCKETS_DIR}")
        display.print("Ensure Frappe Manager services are running.")

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    invalid_services = [s for s in services_to_target if s not in all_services]
    if invalid_services:
        display.error(f"Invalid service name(s): {', '.join(invalid_services)}", exit_code=1)
        display.print(f"Available services: {', '.join(all_services) or 'None'}")

    target_desc = "all services" if not service_names else f"service(s): {display.highlight(', '.join(services_to_target))}"
    process_desc = f"process(es): {display.highlight(', '.join(process_name))}" if process_name else "all processes"


    # --- Stop Execution ---
    display.print(f"\nAttempting to stop {process_desc} in {target_desc}...")
    execute_parallel_command(
        services_to_target,
        util_stop_service,
        action_verb="stopping",
        show_progress=True,
        process_name_list=process_name,
        wait=wait,
        wait_workers=wait_workers
    )

    display.print("\nStop sequence complete.")
