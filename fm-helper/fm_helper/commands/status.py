import sys
from typing import Annotated, Optional, List

import typer
from rich import print

from ..cli import (
    ServiceNameEnumFactory,
    ServiceNameArgument,
    execute_parallel_command,
    get_service_names_for_completion,
    _cached_service_names,
    FM_SUPERVISOR_SOCKETS_DIR,
)
from ..supervisor_utils import get_service_info as util_get_service_info

command_app = typer.Typer(
    help="📊 [yellow]Show status[/yellow] of managed services and their processes.",
    no_args_is_help=True,
)
command_name = "status"

ServiceNamesEnum = ServiceNameEnumFactory()

@command_app.callback(invoke_without_command=True)
def status_cmd(
    service_names: Annotated[Optional[List[ServiceNamesEnum]], ServiceNameArgument],
):
    """Show detailed status of services."""
    if not _cached_service_names:
        print(f"[bold red]Error:[/bold red] No supervisord services found to check status.", file=sys.stderr)
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
    print(f"Fetching status for {target_desc}...")

    execute_parallel_command(
        services_to_target,
        util_get_service_info,
        action_verb="checking status",
        show_progress=False
    )
    print("Status check finished.")
import sys
from typing import Annotated, Optional, List

import typer
from rich import print

from ..cli import (
    ServiceNameEnumFactory,
    ServiceNameArgument,
    execute_parallel_command,
    get_service_names_for_completion,
    _cached_service_names,
    FM_SUPERVISOR_SOCKETS_DIR,
)
from ..supervisor_utils import get_service_info as util_get_service_info

command_app = typer.Typer(
    help="📊 [yellow]Show status[/yellow] of managed services and their processes.",
    no_args_is_help=True,
)
command_name = "status"

ServiceNamesEnum = ServiceNameEnumFactory()

@command_app.callback(invoke_without_command=True)
def status_cmd(
    service_names: Annotated[Optional[List[ServiceNamesEnum]], ServiceNameArgument],
):
    """Show detailed status of services."""
    if not _cached_service_names:
        print(f"[bold red]Error:[/bold red] No supervisord services found to check status.", file=sys.stderr)
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
    print(f"Fetching status for {target_desc}...")

    execute_parallel_command(
        services_to_target,
        util_get_service_info,
        action_verb="checking status",
        show_progress=False
    )
    print("Status check finished.")
