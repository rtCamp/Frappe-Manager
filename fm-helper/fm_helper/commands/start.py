import sys
from typing import Annotated, Optional, List

import typer
from ..display import DisplayManager
from ..command_utils import validate_services, get_process_description
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
            help="Target only specific process(es). If omitted, attempts to start ALL defined processes in the service.",
            show_default=False,
        )
    ] = None,
    state: Annotated[
        Optional[str],
        typer.Option(
            "--state", "-s",
            help="[Ignored unless -p is used] Explicitly start only worker processes matching this state.",
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
    # Get display manager from context dictionary
    display: DisplayManager = ctx.obj['display']

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    valid, target_desc = validate_services(display, services_to_target, all_services, "start")
    if not valid:
        return

    # Validate state if provided
    if state and state not in ("blue", "green"):
        display.error(f"Invalid --state value '{state}'. Must be 'blue' or 'green'.", exit_code=1)

    process_desc = get_process_description(display, process_name)

    # Determine description based on whether specific processes are targeted
    if process_name:
        process_desc = f"specific process(es): {display.highlight(', '.join(process_name))}"
    else:
        process_desc = "all defined processes"

    # Add wait information to the message
    wait_desc = "(with wait)" if wait else "(without wait)"
    display.print(f"\nStarting {process_desc} in {target_desc} {wait_desc}...")

    execute_parallel_command(
        services_to_target,
        util_start_service,
        action_verb="starting",
        show_progress=True,
        process_name_list=process_name,
        wait=wait,
        state=state,
        verbose=verbose
    )

    display.print("\nStart sequence complete.")
