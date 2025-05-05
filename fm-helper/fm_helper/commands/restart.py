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
from ..supervisor.api import (
    restart_service as util_restart_service,
    stop_service as util_stop_service,
    start_service as util_start_service,
)
from ..supervisor.connection import FM_SUPERVISOR_SOCKETS_DIR
from ..supervisor.constants import DEFAULT_SUFFIXES
from ..supervisor.executor import execute_supervisor_command
# Import necessary components for config key management
import json
import contextlib
from pathlib import Path
from ..workers import _update_site_config_key, _get_site_config_key_value

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
    pause_scheduler: Annotated[
        bool,
        typer.Option(
            "--pause-scheduler",
            help="Pause the Frappe scheduler before waiting for jobs and unpause after. Requires --wait-jobs and --site-name.",
        )
    ] = False,
    maintenance_mode: Annotated[
        bool,
        typer.Option(
            "--maintenance-mode",
            help="Set 'maintenance_mode: true' in site_config.json before waiting for jobs and restore original state after waiting. Requires --wait-jobs and --site-name.",
        )
    ] = False,
    wait_workers: Annotated[
        bool,
        typer.Option(
            "--wait-workers/--no-wait-workers",
            help="Use standard stop-then-start restart. Default (--no-wait-workers) uses hybrid rolling restart for worker pairs.",
        )
    ] = False,
    suffixes: Annotated[
        str,
        typer.Option(
            "--suffixes",
            help=f"Comma-separated Blue/Green suffixes for hybrid/rolling restart (default: '{DEFAULT_SUFFIXES}'). Used with --no-wait-workers.",
        )
    ] = DEFAULT_SUFFIXES,
    rolling_timeout: Annotated[
        int,
        typer.Option(
            "--rolling-timeout",
            help="Timeout (seconds) for waiting for new rolling instances during hybrid restart (with --no-wait-workers).",
        )
    ] = 60,
):
    """
    Restart services using either hybrid rolling restart (default) or standard stop-then-start (--wait-workers).
    
    The default mode (--no-wait-workers) uses rolling restart for worker processes and standard restart for others.
    Use --wait-workers to force standard stop-then-start behavior for all processes.
    """
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

    # Variables to store state returned from job waiting / set by this script
    original_scheduler_state: Optional[int] = None
    scheduler_was_paused: bool = False
    original_maintenance_state: Optional[Any] = None # Can be any JSON value or None
    maintenance_was_managed_by_script: bool = False

    # --- Validations ---
    if pause_scheduler and not wait_jobs:
        print("[red]Error:[/red] --pause-scheduler requires --wait-jobs.")
        raise typer.Exit(code=1)

    if maintenance_mode and not wait_jobs:
        print("[red]Error:[/red] --maintenance-mode requires --wait-jobs.")
        raise typer.Exit(code=1)

    if maintenance_mode and not site_name:
        print("[red]Error:[/red] --site-name is required when using --maintenance-mode.")
        raise typer.Exit(code=1)

    if wait_jobs:
        if not site_name:
            print("[red]Error:[/red] --site-name is required when using --wait-jobs, --pause-scheduler, or --maintenance-mode.")
            raise typer.Exit(code=1)
            
        # --- Set Maintenance Mode Key (Moved inside wait_jobs block, before job waiting) ---
        site_config_path = Path(f"/workspace/frappe-bench/sites/{site_name}/site_config.json")

        if maintenance_mode:
            print(f"\n[cyan]Checking and setting 'maintenance_mode' key for site: {site_name}...[/cyan]")
            try:
                # Read current maintenance state using the new helper
                original_maintenance_state = _get_site_config_key_value(
                    site_config_path, "maintenance_mode", default=None, verbose=True
                )

                if original_maintenance_state is not True:
                    _update_site_config_key(site_config_path, "maintenance_mode", True, verbose=True)
                    maintenance_was_managed_by_script = True
                    print(f"[green]'maintenance_mode' key set to true for site: {site_name}.[/green]")
                else:
                    print("[dim]'maintenance_mode' key already set to true. No action taken.[/dim]")

            except Exception as e:
                print(f"[bold red]Error:[/bold red] Failed to check or set 'maintenance_mode' key for site {site_name}: {e}")
                print("Aborting restart.")
                raise typer.Exit(code=1)

        pause_desc = " and pausing scheduler" if pause_scheduler else ""
        maint_desc = " (after setting maintenance mode key)" if maintenance_was_managed_by_script else ""
        print(f"[cyan]Graceful restart: Waiting for active jobs first{pause_desc}{maint_desc}...[/cyan]")
        print("-" * 30)
        # Capture the returned tuple from _run_wait_jobs
        wait_success, original_scheduler_state, scheduler_was_paused = _run_wait_jobs(
            site_name=site_name,
            timeout=wait_jobs_timeout,
            poll_interval=wait_jobs_poll,
            queues=wait_jobs_queue,
            pause_scheduler_during_wait=pause_scheduler
        )
        print("-" * 30)

        if not wait_success:
            print("[bold red]Error:[/bold red] Job waiting failed or timed out.")
            print("Aborting restart process. Services were not stopped or started.")
            raise typer.Exit(code=1)
            
        print("[green]Job waiting successful.[/green]")

        # --- Restore State Immediately After Job Wait (Before Stop/Start) ---
        # Restore Maintenance Mode Key
        if maintenance_was_managed_by_script:
            print(f"\n[cyan]Restoring original 'maintenance_mode' key value ({json.dumps(original_maintenance_state)}) for site '{site_name}' (before stop/start)...[/cyan]", file=sys.stderr)
            try:
                _update_site_config_key(site_config_path, "maintenance_mode", original_maintenance_state, verbose=True)
                print(f"[green]'maintenance_mode' key restored for site '{site_name}'.[/green]", file=sys.stderr)
            except Exception as e:
                print(f"\n[bold yellow]Warning:[/bold yellow] Failed to restore 'maintenance_mode' key in {site_config_path}: {e}", file=sys.stderr)
                print(f"[yellow]Please manually ensure 'maintenance_mode' is set correctly (should be {json.dumps(original_maintenance_state)}) in the file.[/yellow]", file=sys.stderr)

    # --- Restart Execution ---
    job_wait_performed = wait_jobs # Keep track if we waited

    # Determine effective force_kill_timeout
    effective_force_timeout = force_timeout if force else None

    # Choose description based on strategy
    strategy_desc = "Standard Restart (--wait-workers)" if wait_workers else "Hybrid Rolling Restart (default)"
    restart_type = "Forced" if force else "Graceful"
    print(f"\nProceeding with {restart_type} restart ({strategy_desc}) for {target_desc}...")

    try:
        # Execute the restart command in parallel using the single restart function
        execute_parallel_command(
            services_to_target,
            util_restart_service, # Use the single restart function from api.py
            action_verb="restarting",
            show_progress=True,
            # Pass all relevant parameters down
            wait=wait,
            wait_workers=wait_workers, # This flag determines the strategy in _handle_restart
            force_kill_timeout=effective_force_timeout, # Pass timeout only if force is True
            suffixes=suffixes,
            rolling_timeout=rolling_timeout,
        )

        # Success/failure summary is now handled within execute_parallel_command

    finally:
        # --- Restore Scheduler State ---
        if scheduler_was_paused:
            # site_name is guaranteed to be non-None if scheduler_was_paused is True
            print(f"\n[cyan]Restoring original scheduler state ({original_scheduler_state}) for site '{site_name}'...[/cyan]", file=sys.stderr)
            try:
                # Path should already be defined if scheduler_was_paused is True
                _update_site_config_key(site_config_path, "pause_scheduler", original_scheduler_state, verbose=True)
                print(f"[green]Scheduler state restored for site '{site_name}'.[/green]", file=sys.stderr)
            except Exception as e:
                print(f"\n[bold yellow]Warning:[/bold yellow] Failed to automatically restore 'pause_scheduler' key in {site_config_path}: {e}", file=sys.stderr)
                print(f"[yellow]Please manually ensure 'pause_scheduler' is set correctly (should be {json.dumps(original_scheduler_state)}) in the file.[/yellow]", file=sys.stderr)
        # --- End Restore Scheduler State ---

        # Final message indicating the stop/start try block is done
        print(f"\n{restart_type} restart process complete.")
    # --- End of Restart Execution ---
