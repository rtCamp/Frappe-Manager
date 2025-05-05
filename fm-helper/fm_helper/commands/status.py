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
from ..supervisor.api import get_service_info as util_get_service_info
from ..supervisor.connection import FM_SUPERVISOR_SOCKETS_DIR

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
        )
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose", "-v",
            help="Show detailed process information.",
        )
    ] = False,
):
    """Show detailed status of services."""
    # Get display manager from context
    display: DisplayManager = ctx.obj.display

    if not _cached_service_names:
        display.error(f"No supervisord services found to check status.", exit_code=1)
        display.print(f"Looked for socket files in: {FM_SUPERVISOR_SOCKETS_DIR}", file=sys.stderr)
        display.print("Ensure Frappe Manager services are running.", file=sys.stderr)

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    invalid_services = [s for s in services_to_target if s not in all_services]
    if invalid_services:
        display.error(f"Invalid service name(s): {', '.join(invalid_services)}", exit_code=1)
        display.print(f"Available services: {', '.join(all_services) or 'None'}")

    target_desc = "all services" if not service_names else f"service(s): {display.highlight(', '.join(services_to_target))}"
    display.print(f"Fetching status for {target_desc}...")

    execute_parallel_command(
        services_to_target,
        util_get_service_info,
        action_verb="checking status",
        display=display,
        show_progress=False,
        verbose=verbose
    )
    display.print("Status check finished.")
