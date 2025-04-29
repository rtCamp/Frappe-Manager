import sys
from typing import Annotated, Optional, List

import typer
from rich import print

from ..cli import (
    ServiceNameEnumFactory,
    ServiceNameArgument,
    ProcessNameOption,
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
from ..supervisor_utils import stop_service as util_stop_service

command_app = typer.Typer(
    help="🛑 [red]Stop[/red] managed services or specific processes within them. Can optionally wait for active background jobs.",
    no_args_is_help=True,
)
command_name = "stop"

ServiceNamesEnum = ServiceNameEnumFactory()

@command_app.callback(invoke_without_command=True)
def stop_cmd(
    service_names: Annotated[Optional[List[ServiceNamesEnum]], ServiceNameArgument],
    process_name: Annotated[Optional[List[str]], ProcessNameOption],
    wait: Annotated[bool, WaitOption],
    wait_jobs: Annotated[bool, WaitJobsOption],
    site_name: Annotated[Optional[str], SiteNameOption],
    wait_jobs_timeout: Annotated[int, WaitJobsTimeoutOption],
    wait_jobs_poll: Annotated[int, WaitJobsPollOption],
    wait_jobs_queue: Annotated[Optional[List[str]], WaitJobsQueueOption],
):
    """Stop services and optionally wait for jobs to complete."""
    if not _cached_service_names:
        print(f"[bold red]Error:[/bold red] No supervisord services found to stop.", file=sys.stderr)
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

    print(f"Attempting to stop {process_desc} in {target_desc}...")
    execute_parallel_command(
        services_to_target,
        util_stop_service,
        action_verb="stopping",
        show_progress=True,
        process_name_list=process_name,
        wait=wait
    )

    if wait_jobs:
        if not site_name:
            print("[red]Error:[/red] --site-name is required when using --wait-jobs.")
            raise typer.Exit(code=1)
        if process_name:
             print("[yellow]Warning:[/yellow] --wait-jobs checks all jobs for the site, ignoring specific --process flags for the wait.")

        print("-" * 30)
        wait_success = _run_wait_jobs(
            site_name=site_name,
            timeout=wait_jobs_timeout,
            poll_interval=wait_jobs_poll,
            queues=wait_jobs_queue
        )
        print("-" * 30)
        if not wait_success:
             print("[yellow]Continuing stop process despite job wait failure/timeout.[/yellow]")
