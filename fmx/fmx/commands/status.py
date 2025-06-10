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
    # Get display manager from context dictionary
    display: DisplayManager = ctx.obj['display']

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    valid, target_desc = validate_services(display, services_to_target, all_services, "check status")
    if not valid:
        return
    display.print(f"Fetching status for {target_desc}...")

    execute_parallel_command(
        services_to_target,
        util_get_service_info,
        action_verb="checking status",
        show_progress=False,
        verbose=verbose
    )
    display.print("Status check finished.")
