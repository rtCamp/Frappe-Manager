import sys
from typing import Annotated, Optional, List

import typer
from rich import print

from ..cli import (
    ServiceNameEnumFactory,
    execute_parallel_command,
    get_service_names_for_completion,
    _cached_service_names,
    FM_SUPERVISOR_SOCKETS_DIR,
)
from ..supervisor_utils import start_service as util_start_service

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
    ] = True, # Keep existing defaults for options
):
    """Start services or specific processes."""
    if not _cached_service_names:
        print(f"[bold red]Error:[/bold red] No supervisord services found to start.", file=sys.stderr)
        print(f"Looked for socket files in: {FM_SUPERVISOR_SOCKETS_DIR}", file=sys.stderr)
        print("Ensure Frappe Manager services are running.", file=sys.stderr)
        raise typer.Exit(code=1)

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    invalid_services = [s for s in services_to_target if s not in all_services]
    if invalid_services:
        print(f"[red]Error:[/red] Invalid service name(s): {', '.join(invalid_services)}")
        print(f"Available services: {', '.join(all_services) or 'None'}")
        raise typer.Exit(code=1)

    target_desc = "all services" if not service_names else f"service(s): [b cyan]{', '.join(services_to_target)}[/b cyan]"
    process_desc = f"process(es): [b yellow]{', '.join(process_name)}[/b yellow]" if process_name else "all processes"

    print(f"Attempting to start {process_desc} in {target_desc}...")
    execute_parallel_command(
        services_to_target,
        util_start_service,
        action_verb="starting",
        process_name_list=process_name,
        wait=wait
    )
