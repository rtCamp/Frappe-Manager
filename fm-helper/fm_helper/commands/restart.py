import sys
from typing import Annotated, Optional, List

import typer
from rich import print

from ..cli import (
    ServiceNameEnumFactory,
    ServiceNameArgument,
    WaitOption,
    WaitJobsOption,
    SiteNameOption,
    WaitJobsTimeoutOption,
    WaitJobsPollOption,
    WaitJobsQueueOption,
    _run_wait_jobs,
    execute_parallel_command,
    get_service_names_for_completion,
    _cached_service_names,
    FM_SUPERVISOR_SOCKETS_DIR,
)
from ..supervisor_utils import (
    restart_service as util_restart_service,
    stop_service as util_stop_service,
    start_service as util_start_service,
)

command_app = typer.Typer(
    help="🔄 [blue]Restart[/blue] managed services (gracefully by default).",
    no_args_is_help=True,
)
command_name = "restart"

ForceOption = typer.Option(
    False,
    "--force",
    "-f",
    help="Force restart using supervisor's internal restart (if available). Defaults to graceful stop/start.",
)

ServiceNamesEnum = ServiceNameEnumFactory()

@command_app.callback(invoke_without_command=True)
def restart_cmd(
    service_names: Annotated[Optional[List[ServiceNamesEnum]], ServiceNameArgument],
    wait_jobs: Annotated[bool, WaitJobsOption],
    site_name: Annotated[Optional[str], SiteNameOption],
    wait_jobs_timeout: Annotated[int, WaitJobsTimeoutOption],
    wait_jobs_poll: Annotated[int, WaitJobsPollOption],
    wait_jobs_queue: Annotated[Optional[List[str]], WaitJobsQueueOption],
    force: Annotated[bool, ForceOption],
    wait: Annotated[bool, WaitOption] = True,
):
    """Restart services with optional job waiting."""
    if not _cached_service_names:
        print(f"[bold red]Error:[/bold red] No supervisord services found to restart.", file=sys.stderr)
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
    restart_type = "Forced" if force else "Graceful"
    print(f"Attempting {restart_type} restart for {target_desc}...")

    wait_jobs_active = wait_jobs and not force

    if wait_jobs_active:
        if not site_name:
            print("[red]Error:[/red] --site-name is required when using --wait-jobs for graceful restart.")
            raise typer.Exit(code=1)
        print("[cyan]Graceful restart with job waiting enabled.[/cyan]")

        print("\n[STEP 1/3] Stopping services...")
        execute_parallel_command(
            services_to_target,
            util_stop_service,
            action_verb="stopping",
            show_progress=True,
            wait=wait
        )

        print("\n[STEP 2/3] Waiting for active jobs...")
        print("-" * 30)
        wait_success = _run_wait_jobs(
            site_name=site_name,
            timeout=wait_jobs_timeout,
            poll_interval=wait_jobs_poll,
            queues=wait_jobs_queue
        )
        print("-" * 30)
        if not wait_success:
             print("[yellow]Continuing restart process despite job wait failure/timeout.[/yellow]")

        print("\n[STEP 3/3] Starting services...")
        execute_parallel_command(
            services_to_target,
            util_start_service,
            action_verb="starting",
            show_progress=True,
            wait=wait
        )
        print("\nGraceful restart sequence complete.")

    else:
        if wait_jobs and force:
            print("[yellow]Warning:[/yellow] --wait-jobs is ignored when using --force restart.")

        execute_parallel_command(
            services_to_target,
            util_restart_service,
            action_verb="restarting",
            show_progress=True,
            force=force,
            wait=wait
        )
import sys
from typing import Annotated, Optional, List

import typer
from rich import print

from ..cli import (
    ServiceNameEnumFactory,
    ServiceNameArgument,
    WaitOption,
    WaitJobsOption,
    SiteNameOption,
    WaitJobsTimeoutOption,
    WaitJobsPollOption,
    WaitJobsQueueOption,
    _run_wait_jobs,
    execute_parallel_command,
    get_service_names_for_completion,
    _cached_service_names,
    FM_SUPERVISOR_SOCKETS_DIR,
)
from ..supervisor_utils import (
    restart_service as util_restart_service,
    stop_service as util_stop_service,
    start_service as util_start_service,
)

command_app = typer.Typer(
    help="🔄 [blue]Restart[/blue] managed services (gracefully by default).",
    no_args_is_help=True,
)
command_name = "restart"

ForceOption = typer.Option(
    False,
    "--force",
    "-f",
    help="Force restart using supervisor's internal restart (if available). Defaults to graceful stop/start.",
)

ServiceNamesEnum = ServiceNameEnumFactory()

@command_app.callback(invoke_without_command=True)
def restart_cmd(
    service_names: Annotated[Optional[List[ServiceNamesEnum]], ServiceNameArgument],
    wait_jobs: Annotated[bool, WaitJobsOption],
    site_name: Annotated[Optional[str], SiteNameOption],
    wait_jobs_timeout: Annotated[int, WaitJobsTimeoutOption],
    wait_jobs_poll: Annotated[int, WaitJobsPollOption],
    wait_jobs_queue: Annotated[Optional[List[str]], WaitJobsQueueOption],
    force: Annotated[bool, ForceOption],
    wait: Annotated[bool, WaitOption] = True,
):
    """Restart services with optional job waiting."""
    if not _cached_service_names:
        print(f"[bold red]Error:[/bold red] No supervisord services found to restart.", file=sys.stderr)
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
    restart_type = "Forced" if force else "Graceful"
    print(f"Attempting {restart_type} restart for {target_desc}...")

    wait_jobs_active = wait_jobs and not force

    if wait_jobs_active:
        if not site_name:
            print("[red]Error:[/red] --site-name is required when using --wait-jobs for graceful restart.")
            raise typer.Exit(code=1)
        print("[cyan]Graceful restart with job waiting enabled.[/cyan]")

        print("\n[STEP 1/3] Stopping services...")
        execute_parallel_command(
            services_to_target,
            util_stop_service,
            action_verb="stopping",
            show_progress=True,
            wait=wait
        )

        print("\n[STEP 2/3] Waiting for active jobs...")
        print("-" * 30)
        wait_success = _run_wait_jobs(
            site_name=site_name,
            timeout=wait_jobs_timeout,
            poll_interval=wait_jobs_poll,
            queues=wait_jobs_queue
        )
        print("-" * 30)
        if not wait_success:
             print("[yellow]Continuing restart process despite job wait failure/timeout.[/yellow]")

        print("\n[STEP 3/3] Starting services...")
        execute_parallel_command(
            services_to_target,
            util_start_service,
            action_verb="starting",
            show_progress=True,
            wait=wait
        )
        print("\nGraceful restart sequence complete.")

    else:
        if wait_jobs and force:
            print("[yellow]Warning:[/yellow] --wait-jobs is ignored when using --force restart.")

        execute_parallel_command(
            services_to_target,
            util_restart_service,
            action_verb="restarting",
            show_progress=True,
            force=force,
            wait=wait
        )
