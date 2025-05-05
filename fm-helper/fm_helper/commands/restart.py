import sys
from typing import Annotated, Optional, List
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
    service_names: Annotated[
        Optional[List[ServiceNamesEnum]],
        typer.Argument(
            help="Name(s) of the service(s) to restart. If omitted, targets ALL running services.",
            autocompletion=get_service_names_for_completion,
            show_default=False,
        )
    ] = None,
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
    """Restart services using standard supervisor stop-then-start.

    Optionally uses RQ's Redis-based suspension (--suspend-rq) and waits for
    workers to suspend (--wait-workers) before restarting.

    [bold]Workflow:[/bold]
    1. (Optional) Sets the 'rq:suspended' flag in Redis, verifies it, enqueues noop jobs.
    2. (Optional) Waits for RQ workers to reach the 'suspended' state.
    3. Restarts all target services using standard supervisor stop-then-start.
    4. (Finally) Removes the 'rq:suspended' flag from Redis.
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
    print(f"Attempting Restart for {target_desc}...")

    # --- STEP 1: Suspend Workers (via Redis Flag) ---
    is_waiting_workers = wait_workers
    suspension_needed = suspend_rq or is_waiting_workers
    if suspension_needed:
        print("\n[bold cyan]➡️ Suspending RQ Workers (Redis Flag)[/bold cyan]")
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

    resume_called = False # Flag to ensure resume is called only once


    # --- Restart Execution ---
    print(f"\n[bold cyan]🔄 Restarting Services[/bold cyan] ({target_desc}, standard stop-then-start)...")

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
            print("\n[bold cyan]🟢 Resuming RQ Workers (Redis Flag)[/bold cyan]", file=sys.stderr)
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
