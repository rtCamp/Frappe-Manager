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
from ..supervisor import (
    restart_service as util_restart_service,
    stop_service as util_stop_service,
    start_service as util_start_service,
    FM_SUPERVISOR_SOCKETS_DIR,
)

command_name = "restart"

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
    force: Annotated[
        bool,
        typer.Option(
            "--force", "-f",
            help="Force stop/start. Tries graceful stop first, then force kills if timeout is reached.",
        )
    ] = False,
    force_timeout: Annotated[
        int,
        typer.Option(
            "--force-timeout",
            help="Timeout (seconds) to wait for graceful stop during --force before killing (default: 10).",
        )
    ] = 10,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Wait for supervisor start/stop operations to complete before returning.",
        )
    ] = True,
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
    proceed_with_restart = True

    if wait_jobs_active:
        if not site_name:
            print("[red]Error:[/red] --site-name is required when using --wait-jobs.")
            raise typer.Exit(code=1)
        
        print("[cyan]Graceful restart: Waiting for active jobs first...[/cyan]")
        print("-" * 30)
        wait_success = _run_wait_jobs(
            site_name=site_name,
            timeout=wait_jobs_timeout,
            poll_interval=wait_jobs_poll,
            queues=wait_jobs_queue
        )
        print("-" * 30)

        if not wait_success:
            print("[bold red]Error:[/bold red] Job waiting failed or timed out.")
            print("Aborting restart process. Services were not stopped or started.")
            raise typer.Exit(code=1)
        
        print("[green]Job waiting successful. Proceeding with service restart.[/green]")

    if wait_jobs and force:
        print("[yellow]Warning:[/yellow] --wait-jobs was specified but ignored due to --force.")

    if force:
        print("\n[STEP 1/2] Force stopping services...")
        execute_parallel_command(
            services_to_target,
            util_stop_service,
            action_verb="stopping",
            show_progress=True,
            wait=wait,
            force_kill_timeout=force_timeout
        )

        print("\n[STEP 2/2] Force starting services...")
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

        if force:
            # Forced restart logic
            print("\n[STEP 1/2] Force stopping services...")
            execute_parallel_command(
                services_to_target,
                util_stop_service,
                action_verb="stopping",
                show_progress=True,
                wait=wait,
                force_kill_timeout=force_timeout
            )

            print("\n[STEP 2/2] Force starting services...")
            execute_parallel_command(
                services_to_target,
                util_start_service,
                action_verb="starting",
                show_progress=True,
                wait=wait
            )
            print("\nForced restart sequence complete.")
        else:
            # Simple restart or graceful restart (after successful job wait)
            step_prefix = "[STEP 1/2]" if not wait_jobs_active else "[STEP 2/3]"
            print(f"\n{step_prefix} Stopping services...")
            execute_parallel_command(
                services_to_target,
                util_stop_service,
                action_verb="stopping",
                show_progress=True,
                wait=wait
            )

            step_prefix = "[STEP 2/2]" if not wait_jobs_active else "[STEP 3/3]"
            print(f"\n{step_prefix} Starting services...")
            execute_parallel_command(
                services_to_target,
                util_start_service,
                action_verb="starting",
                show_progress=True,
                wait=wait
            )
            restart_type = "Graceful" if wait_jobs_active else "Simple"
            print(f"\n{restart_type} restart sequence complete.")
