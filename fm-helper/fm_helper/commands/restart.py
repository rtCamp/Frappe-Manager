# New restart.py containing the suspend-based restart logic
import sys
import signal
import os
from typing import Annotated, Optional, List, Tuple, Any
from ..rq_controller import (
    control_rq_workers, 
    check_rq_suspension, 
    wait_for_rq_workers_suspended,
    ActionEnum
)
from ..supervisor.executor import execute_supervisor_command


import typer
from rich import print

# Use relative imports within the package
try:
    from ..cli import (
        ServiceNameEnumFactory,
        _run_wait_jobs,
        execute_parallel_command,
        get_service_names_for_completion,
        _cached_service_names,
    )
    from ..supervisor.api import (
        restart_service as util_restart_service,
        signal_service as util_signal_service,
        get_service_info as util_get_service_info,
    )
    from ..supervisor.connection import FM_SUPERVISOR_SOCKETS_DIR
    from ..supervisor.constants import is_worker_process, ProcessStates
    from ..supervisor.exceptions import SupervisorError, SupervisorConnectionError
    # Import necessary components for config key management
    import json
    import contextlib
    from pathlib import Path
    from ..workers import _update_site_config_key, _get_site_config_key_value
except ImportError as e:
    print(f"[bold red]Error:[/bold red] Failed to import required modules: {e}")
    print("Ensure fm-helper structure is correct and dependencies are installed.")
    sys.exit(1)


command_name = "restart"

ServiceNamesEnum = ServiceNameEnumFactory()

# --- Helper to get worker process names for a service ---
def _get_worker_processes_for_service(service_name: str) -> List[str]:
    """Fetches process info and returns names of worker processes."""
    worker_names = []
    try:
        # Use execute_parallel_command with a placeholder action 'INFO'
        process_info_results = execute_parallel_command(
            [service_name],
            lambda s, **kw: execute_supervisor_command(s, action="info"),
            action_verb="getting info for",
            show_progress=False,
            action='INFO'
        )

        info_list = process_info_results.get(service_name) if isinstance(process_info_results, dict) else None

        if isinstance(info_list, list):
             for proc_info in info_list:
                 if 'name' in proc_info and is_worker_process(proc_info['name']):
                     worker_names.append(proc_info['name'])
        elif info_list is None and process_info_results is not None and service_name in process_info_results:
             print(f"[yellow]Warning:[/yellow] Could not retrieve process info for {service_name} to identify workers (result was None).")
        elif info_list is None:
             pass # Error already logged

    except SupervisorConnectionError:
         print(f"[yellow]Warning:[/yellow] Could not connect to {service_name} to identify workers.")
    except Exception as e:
        print(f"[red]Error identifying workers for {service_name}: {e}[/red]")
        import traceback
        traceback.print_exc()

    return worker_names


# --- Command Definition ---
def command(
    ctx: typer.Context,
    site_name: Annotated[
        Optional[str],
        typer.Option(
            "--site-name",
            help="Frappe site name. [bold yellow]Required only if --wait-jobs is used[/bold yellow] (or related options like --pause-scheduler, --maintenance-mode).",
        )
    ] = None,
    service_names: Annotated[
        Optional[List[ServiceNamesEnum]],
        typer.Argument(
            help="Name(s) of the service(s) to restart. If omitted, targets ALL running services.",
            autocompletion=get_service_names_for_completion,
            show_default=False,
        )
    ] = None,
    wait_jobs: Annotated[
        bool,
        typer.Option(
            "--wait-jobs",
            help="Wait for active Frappe background jobs ('started' state) to finish after suspending workers.",
        )
    ] = False,
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
    maintenance_mode: Annotated[
        bool,
        typer.Option(
            "--maintenance-mode",
            help="Set 'maintenance_mode: true' in site_config.json before waiting for jobs and restore original state after waiting. Requires --wait-jobs and --site-name.",
        )
    ] = False,
    suspend_rq: Annotated[
        bool,
        typer.Option(
            "--suspend-rq",
            help="Suspend RQ workers via Redis flag before restarting. Requires Redis connection info in common_site_config.json.",
        )
    ] = False,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Wait for the final supervisor restart operations to complete before returning.",
        )
    ] = True,
    wait_workers: Annotated[
        bool,
        typer.Option(
            "--wait-workers",
            help="Wait for RQ workers to become idle/suspended before restarting. Mutually exclusive with --wait-jobs. Implies --suspend-rq.",
        )
    ] = False,
    wait_workers_timeout: Annotated[
        int,
        typer.Option(
            "--wait-workers-timeout",
            help="Timeout (seconds) for --wait-workers (default: 300).",
        )
    ] = 300,
    wait_workers_poll: Annotated[
        int,
        typer.Option(
            "--wait-workers-poll", 
            help="Polling interval (seconds) for --wait-workers (default: 5).",
        )
    ] = 5,
    wait_workers_verbose: Annotated[
        bool,
        typer.Option(
            "--wait-workers-verbose",
            help="Show detailed worker states during --wait-workers checks.",
        )
    ] = False,
):
    """
    Restart services using standard supervisor stop-then-start.

    Supports two methods of graceful worker shutdown:
    1. Redis-based suspension (--suspend-rq or --wait-workers)
    2. Job completion waiting (--wait-jobs with optional --pause-scheduler)

    [bold]Workflow:[/bold]
    1. (Optional, if --suspend-rq) Sets the 'rq:suspended' flag in Redis, verifies it.
    2. (Optional, if --wait-jobs) Sets 'maintenance_mode: true' in common_site_config.json (--maintenance-mode).
    3. (Optional, if --wait-jobs) Waits for Frappe background jobs to complete,
       potentially pausing the scheduler (--pause-scheduler).
    4. (Optional, if --wait-jobs) Restores the original 'maintenance_mode' value.
    5. Restarts all target services using standard supervisor stop-then-start.
    6. (Finally, if --suspend-rq) Removes the 'rq:suspended' flag from Redis.
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
    print(f"Attempting Restart (Suspend + Wait + Restart) for {target_desc}...")

    # --- STEP 1: Suspend Workers (via Redis Flag) ---
    is_waiting_workers = wait_workers
    suspension_needed = suspend_rq or is_waiting_workers
    if suspension_needed:
        print("\n[Optional Step] Suspending RQ workers via Redis flag...")
        try:
            # Call without site parameter
            success = control_rq_workers(action=ActionEnum.suspend)

            if not success:
                print("[bold red]Error:[/bold red] Failed to suspend RQ workers via Redis.")
                print("Check logs above for details from rq_controller.")
                print("Aborting restart.")
                raise typer.Exit(code=1) # Raise Exit from restart command
            else:
                # Success message is printed by control_rq_workers
                print("[green]RQ workers suspended via Redis flag.[/green]") # Keep overall status

                # --- Verify Suspension ---
                print("[dim]Verifying suspension status...[/dim]")
                suspension_status = check_rq_suspension()

                if suspension_status is True:
                    print("[green]Verification successful: RQ suspension flag is set in Redis.[/green]")
                elif suspension_status is False:
                    print("[bold red]Error:[/bold red] Verification failed: RQ suspension flag was NOT found in Redis after attempting to set it.")
                    print("Aborting restart.")
                    raise typer.Exit(code=1)
                else: # suspension_status is None
                    print("[bold red]Error:[/bold red] Could not verify suspension status due to an error during the check.")
                    print("Check logs above for details from rq_controller check.")
                    print("Aborting restart.")
                    raise typer.Exit(code=1)
                # --- End Verification ---

                # Optional: Add message indicating noop jobs were handled
                print("[dim]Noop jobs enqueued (if applicable). Proceeding...[/dim]")

                # If --wait-workers is active, wait for workers to complete
                if wait_workers:
                    print("\n[cyan]Waiting for RQ workers to complete their current jobs...[/cyan]")
                    if not wait_for_rq_workers_suspended(
                        timeout=wait_workers_timeout,
                        poll_interval=wait_workers_poll,
                        verbose=wait_workers_verbose
                    ):
                        print("[red]Error:[/red] Workers did not become idle within the timeout period.")
                        print("Aborting restart to avoid interrupting jobs.")
                        # Resume workers before exiting
                        control_rq_workers(action=ActionEnum.resume)
                        raise typer.Exit(code=1)

        except Exception as e: # Catch unexpected errors *calling* control_rq_workers OR check_rq_suspension
            print(f"[bold red]Error:[/bold red] An unexpected error occurred during worker suspension or verification: {e}")
            import traceback
            traceback.print_exc()
            raise typer.Exit(code=1)

    # --- End STEP 1 ---

    # Variables to store state set by this script
    original_maintenance_state: Optional[Any] = None # Can be any JSON value or None
    maintenance_was_managed_by_script: bool = False

    # --- Validations ---
    if wait_jobs and not site_name:
        print("[red]Error:[/red] --site-name is required when using --wait-jobs.")
        raise typer.Exit(code=1)

    if pause_scheduler and not wait_jobs:
        print("[red]Error:[/red] --pause-scheduler requires --wait-jobs.")
        raise typer.Exit(code=1)

    if maintenance_mode and not wait_jobs:
        print("[red]Error:[/red] --maintenance-mode requires --wait-jobs.")
        raise typer.Exit(code=1)
        
    if wait_workers is not None and wait_jobs:
        print("[red]Error:[/red] --wait-workers and --wait-jobs are mutually exclusive.")
        print("Use --wait-workers for simple worker completion waiting, or")
        print("--wait-jobs for full job queue monitoring with optional scheduler pause.")
        raise typer.Exit(code=1)

    resume_called = False # Flag to ensure resume is called only once

    if wait_jobs:
        # --- Set Maintenance Mode Key (Moved inside wait_jobs block, before job waiting) ---
        common_config_path = Path("/workspace/frappe-bench/sites/common_site_config.json")

        if maintenance_mode:
            print(f"\n[cyan]Checking and setting 'maintenance_mode' key for site: {site_name}...[/cyan]")
            try:
                # Read current maintenance state using the helper
                original_maintenance_state = _get_site_config_key_value("maintenance_mode", default=None, verbose=True)

                if original_maintenance_state is not True:
                    _update_site_config_key("maintenance_mode", True, verbose=True)
                    maintenance_was_managed_by_script = True
                    print(f"[green]'maintenance_mode' key set to true in {common_config_path}.[/green]")
                else:
                    print(f"[dim]'maintenance_mode' key already set to true in {common_config_path}. No action taken.[/dim]")

            except Exception as e:
                print(f"[bold red]Error:[/bold red] Failed to check or set 'maintenance_mode' key for site {site_name}: {e}")
                print("Aborting restart.")
                raise typer.Exit(code=1)

        pause_desc = " and pausing scheduler" if pause_scheduler else ""
        maint_desc = " (after setting maintenance mode key)" if maintenance_was_managed_by_script else ""
        print(f"[cyan][Optional Step] Waiting for active jobs{pause_desc}{maint_desc}...[/cyan]")
        print("-" * 30)
        # Call wait_jobs function
        wait_success = _run_wait_jobs(
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
            print(f"\n[cyan]Restoring original 'maintenance_mode' key value ({json.dumps(original_maintenance_state)}) in {common_config_path} (before stop/start)...[/cyan]", file=sys.stderr)
            try:
                _update_site_config_key("maintenance_mode", original_maintenance_state, verbose=True)
                print(f"[green]'maintenance_mode' key restored in {common_config_path}.[/green]", file=sys.stderr)
            except Exception as e:
                print(f"\n[bold yellow]Warning:[/bold yellow] Failed to restore 'maintenance_mode' key in {common_config_path}: {e}", file=sys.stderr)
                print(f"[yellow]Please manually ensure 'maintenance_mode' is set correctly (should be {json.dumps(original_maintenance_state)}) in the file.[/yellow]", file=sys.stderr)

    # --- Restart Execution ---
    job_wait_performed = wait_jobs # Keep track if we waited

    print(f"\n[Main Step] Restarting {target_desc} (standard stop-then-start)...")

    try:
        # Execute the restart command in parallel using the single restart function
        execute_parallel_command(
            services_to_target,
            util_restart_service, # Use the single restart function from api.py
            action_verb="restarting",
            show_progress=True,
            # Pass all relevant parameters down
            wait=wait,
            wait_workers=True, # Force standard stop-then-start strategy
            force_kill_timeout=None, # No forced kill in this flow
        )

        # Success/failure summary is now handled within execute_parallel_command

    finally:

        # --- Resume Workers ---
        if suspension_needed and not resume_called:
            print("\n[cyan]Attempting to resume RQ workers via Redis flag...[/cyan]", file=sys.stderr)
            try:
                # Call without site parameter
                success = control_rq_workers(action=ActionEnum.resume)

                if not success:
                    print(f"[bold yellow]Warning:[/bold yellow] Failed to resume RQ workers via Redis flag. Check logs above.", file=sys.stderr)
                    print("[yellow]You may need to manually remove the 'rq:suspended' key in Redis if workers remain suspended.[/yellow]", file=sys.stderr)
                # else: Success message printed by control_rq_workers

            except Exception as e: # Catch unexpected errors *calling* rq_controller_main
                 print(f"[bold red]Error:[/bold red] An unexpected error occurred while trying to call rq_controller to resume workers: {e}", file=sys.stderr)
                 import traceback
                 traceback.print_exc(file=sys.stderr)
            finally:
                 resume_called = True # Mark as attempted regardless of outcome
        # Final message indicating the stop/start try block is done
        print(f"\nRestart process complete for {target_desc}.")
    # --- End of Restart Execution ---
