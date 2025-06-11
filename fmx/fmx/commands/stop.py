from typing import Annotated, List, Optional

import typer

from ..cli import (
    ServiceNameEnumFactory,
    execute_parallel_command,
    get_service_names_for_completion,
)
from ..command_utils import get_process_description, validate_services
from ..display import DisplayManager
from ..supervisor import stop_service as util_stop_service

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
    display: DisplayManager = ctx.obj['display']

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    valid, target_desc = validate_services(display, services_to_target, all_services, "stop")
    if not valid:
        return

    process_desc = get_process_description(display, process_name)

    # Add wait information to the message
    wait_desc = "(with wait)" if wait else "(without wait)"

    display.print(f"\nAttempting to stop {process_desc} in {target_desc} {wait_desc}...")
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
