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
from ..supervisor.api import start_service as util_start_service
from ..supervisor.connection import FM_SUPERVISOR_SOCKETS_DIR

command_name = "start"

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
            "--process",
            "-p",
            help="Target only specific process(es). If omitted, attempts to start ALL non-worker processes and the ACTIVE worker state (or the state specified by --state).",
            show_default=False,
        )
    ] = None,
    state: Annotated[
        Optional[str],
        typer.Option(
            "--state", "-s",
            help="Explicitly start only worker processes matching this state (e.g., 'blue' or 'green'). Overrides automatic active state detection. Only applies when specific processes (-p) are NOT given.",
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
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose", "-v",
            help="Show detailed process identification and skipping messages during start.",
        )
    ] = False,
):
    """Start services or specific processes."""
    # Get display manager from context
    display: DisplayManager = ctx.obj.display

    if not _cached_service_names:
        display.error(f"No supervisord services found to start.", exit_code=1)
        display.print(f"Looked for socket files in: {FM_SUPERVISOR_SOCKETS_DIR}", file=sys.stderr)
        display.print("Ensure Frappe Manager services are running.", file=sys.stderr)

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    invalid_services = [s for s in services_to_target if s not in all_services]
    if invalid_services:
        display.error(f"Invalid service name(s): {', '.join(invalid_services)}", exit_code=1)
        display.print(f"Available services: {', '.join(all_services) or 'None'}")

    # Validate state if provided
    if state and state not in ("blue", "green"):
        display.error(f"Invalid --state value '{state}'. Must be 'blue' or 'green'.", exit_code=1)

    target_desc = "all services" if not service_names else f"service(s): {display.highlight(', '.join(services_to_target))}"

    # Determine description based on whether specific processes or state are targeted
    if process_name:
        process_desc = f"specific process(es): {display.highlight(', '.join(process_name))}"
    elif state:
        process_desc = f"non-workers and {display.highlight(state)} worker processes"
    else:
        process_desc = "all defined non-workers and the active worker state"

    display.print(f"\nStarting {process_desc} in {target_desc}...")

    execute_parallel_command(
        services_to_target,
        util_start_service,
        action_verb="starting",
        display=display,
        show_progress=True,
        process_name_list=process_name,
        wait=wait,
        state=state,
        verbose=verbose
    )

    display.print("\nStart sequence complete.")
