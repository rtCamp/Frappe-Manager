from typing import Annotated, Optional, List

import typer
from fmx.display import DisplayManager
from fmx.command_utils import validate_services, get_process_description, resolve_service_targets, format_wait_desc
from fmx.cli import (
    ServiceNameEnumFactory,
    execute_parallel_command,
    get_service_names_for_completion,
)
from fmx.supervisor.api import start_service as util_start_service

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
        ),
    ] = None,
    process_name: Annotated[
        Optional[List[str]],
        typer.Option(
            "--process",
            "-p",
            help="Target only specific process(es). If omitted, attempts to start ALL defined processes in the service.",
            show_default=False,
        ),
    ] = None,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Wait for supervisor start operations to complete before returning.",
        ),
    ] = True,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show detailed process identification and skipping messages during start.",
        ),
    ] = False,
):
    """Start services or specific processes."""

    display: DisplayManager = ctx.obj['display']

    all_services = get_service_names_for_completion()
    services_to_target = resolve_service_targets(service_names, all_services)

    valid, target_desc = validate_services(display, services_to_target, all_services, "start")
    if not valid:
        return

    process_desc = get_process_description(display, process_name)
    wait_desc = format_wait_desc(wait)
    display.print(f"\nStarting {process_desc} in {target_desc} {wait_desc}...")

    execute_parallel_command(
        services_to_target,
        util_start_service,
        action_verb="starting",
        show_progress=True,
        process_name_list=process_name,
        wait=wait,
        verbose=verbose,
    )

    display.print("\nStart sequence complete.")
