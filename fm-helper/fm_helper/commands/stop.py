import sys
from typing import Annotated, Optional, List

import typer
from rich import print

from ..cli import (
    ServiceNameEnumFactory,
    _run_wait_jobs,
    execute_parallel_command,
    get_service_names_for_completion,
    _cached_service_names,
)
from ..supervisor import stop_service as util_stop_service, FM_SUPERVISOR_SOCKETS_DIR

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
    wait_jobs: Annotated[
        bool,
        typer.Option(
            "--wait-jobs",
            help="Wait for active Frappe background jobs ('started' state) to finish before completing stop/restart.",
        )
    ] = False,
    site_name: Annotated[
        Optional[str],
        typer.Option(
            "--site-name",
            help="Frappe site name (required if --wait-jobs is used).",
        )
    ] = None,
    wait_jobs_timeout: Annotated[
        int,
        typer.Option(
            "--wait-jobs-timeout",
            help="Timeout (seconds) for waiting for jobs (default: 300).",
        )
    ] = 300,
    wait_jobs_poll: Annotated[
        int,
        typer.Option(
            "--wait-jobs-poll",
            help="Polling interval (seconds) for checking jobs (default: 5).",
        )
    ] = 5,
    wait_jobs_queue: Annotated[
        Optional[List[str]],
        typer.Option(
            "--queue", "-q",
            help="Specific job queue(s) to monitor when using --wait-jobs. Use multiple times (e.g., -q short -q long). Monitors all if not specified.",
        )
    ] = None,
    pause_scheduler: Annotated[
        bool,
        typer.Option(
            "--pause-scheduler",
            help="Pause the Frappe scheduler before waiting for jobs and unpause after. Requires --wait-jobs and --site-name.",
        )
    ] = False,
    wait_workers: Annotated[
        bool,
        typer.Option(
            "--wait-workers/--no-wait-workers",
            help="Explicitly wait for processes containing '-worker' to stop gracefully during the stop sequence (primarily relevant with --force-timeout).",
        )
    ] = False,
):
    """Stop services, optionally wait for jobs, optionally pause scheduler, and optionally wait specifically for workers."""
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

    # --- Job Waiting Logic (Before Stop) ---
    wait_success = True # Assume success if not waiting
    if wait_jobs:
        if not site_name:
            print("[red]Error:[/red] --site-name is required when using --wait-jobs (and --pause-scheduler).")
            raise typer.Exit(code=1)
        if pause_scheduler and not wait_jobs:
            print("[red]Error:[/red] --pause-scheduler can only be used with --wait-jobs.")
            raise typer.Exit(code=1)
        if process_name:
             print("[yellow]Warning:[/yellow] --wait-jobs checks all jobs for the site, ignoring specific --process flags for the wait.")

        pause_desc = " and pausing scheduler" if pause_scheduler else ""
        print(f"[cyan]Waiting for active jobs first{pause_desc}...[/cyan]")
        print("-" * 30)
        wait_success = _run_wait_jobs(
            site_name=site_name,
            timeout=wait_jobs_timeout,
            poll_interval=wait_jobs_poll,
            queues=wait_jobs_queue,
            pause_scheduler_during_wait=pause_scheduler
        )
        print("-" * 30)
        if not wait_success:
             print("[yellow]Continuing stop process despite job wait failure/timeout.[/yellow]")

    # --- Stop Execution ---
    print(f"\nAttempting to stop {process_desc} in {target_desc}...")
    execute_parallel_command(
        services_to_target,
        util_stop_service,
        action_verb="stopping",
        show_progress=True,
        process_name_list=process_name,
        wait=wait,
        wait_workers=wait_workers
    )

    print("\nStop sequence complete.")
