import sys
import time
from typing import Annotated, Optional, List
from ..supervisor.constants import STOPPED_STATES
from ..rq_controller import (
    control_rq_workers, 
    check_rq_suspension, 
    wait_for_rq_workers_suspended,
    ActionEnum
)
from ..display import DisplayManager
from ..command_utils import validate_services, get_process_description

import typer

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
        signal_service_workers as util_signal_service_workers,
        get_service_info as util_get_service_info
    )
    from ..supervisor.connection import FM_SUPERVISOR_SOCKETS_DIR
except ImportError as e:
    sys.stderr.write(f"Error: Failed to import required modules: {e}\n")
    sys.stderr.write("Ensure fm-helper structure is correct and dependencies are installed.\n")
    sys.exit(1)


command_name = "restart"

ServiceNamesEnum = ServiceNameEnumFactory()

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
        Optional[bool],
        typer.Option(
            "--wait-workers/--no-wait-workers",
            help="Wait for RQ workers to become idle/suspended before restarting. Implies --suspend-rq.",
            show_default=False,
        )
    ] = None,
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
    wait_after_signal_timeout: Annotated[
        int,
        typer.Option(
            "--wait-after-signal-timeout",
            help="Timeout (seconds) for waiting after signaling workers (default: 60).",
        )
    ] = 60,
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
    # Get display manager from context dictionary
    display: DisplayManager = ctx.obj['display']

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    valid, target_desc = validate_services(display, services_to_target, all_services, "restart")
    if not valid:
        return
    display.print(f"Attempting Restart for {target_desc}...")

    # --- STEP 1: Suspend Workers (via Redis Flag) ---
    is_waiting_workers = wait_workers is True
    suspension_needed = suspend_rq or is_waiting_workers
    if suspension_needed:
        display.heading("➡️ Suspending RQ Workers (Redis Flag)")
        try:
            # Call without site parameter
            success = control_rq_workers(action=ActionEnum.suspend)

            if not success:
                display.error("Failed to suspend RQ workers via Redis.", exit_code=1)
                display.print("Check logs above for details from rq_controller.")
                display.print("Aborting restart.")
            else:
                # Success message is printed by control_rq_workers
                display.success("RQ workers suspended via Redis flag.")

                # --- Verify Suspension ---
                display.dimmed("Verifying suspension status...")
                suspension_status = check_rq_suspension()

                if suspension_status is True:
                    display.success("Verification successful: RQ suspension flag is set in Redis.")
                elif suspension_status is False:
                    display.error("Verification failed: RQ suspension flag was NOT found in Redis after attempting to set it.")
                    display.print("Aborting restart.")
                    raise typer.Exit(code=1)
                else: # suspension_status is None
                    display.error("Could not verify suspension status due to an error during the check.")
                    display.print("Check logs above for details from rq_controller check.")
                    display.print("Aborting restart.")
                    raise typer.Exit(code=1)
                # --- End Verification ---

                # If --wait-workers is active, wait for workers to complete
                if wait_workers is True:
                    display.print("\n[cyan]Waiting for RQ workers to complete their current jobs...[/cyan]")
                    if not wait_for_rq_workers_suspended(
                        timeout=wait_workers_timeout,
                        poll_interval=wait_workers_poll,
                        verbose=wait_workers_verbose
                    ):
                        display.error("Workers did not become idle within the timeout period.")
                        display.print("Aborting restart to avoid interrupting jobs.")
                        # Resume workers before exiting
                        control_rq_workers(action=ActionEnum.resume)
                        raise typer.Exit(code=1)

        except Exception as e: # Catch unexpected errors *calling* control_rq_workers OR check_rq_suspension
            display.error(f"An unexpected error occurred during worker suspension or verification: {e}")
            import traceback
            traceback.print_exc()
            raise typer.Exit(code=1)

    # --- End STEP 1 ---

    # --- STEP 2: Signal Workers (if using --no-wait-workers) ---
    is_signaling_workers = wait_workers is False
    if is_signaling_workers:
        display.heading("➡️ Signaling Workers for Graceful Shutdown")
        try:
            # Signal workers in all target services
            for service_name in services_to_target:
                signaled_workers = util_signal_service_workers(service_name)
                if signaled_workers:
                    display.success(f"Signaled workers in {display.highlight(service_name)}: {', '.join(signaled_workers)}")
                else:
                    display.dimmed(f"No worker processes found to signal in {display.highlight(service_name)}")

            # Let bench-wrapper.sh handle the worker lifecycle
            # The monitor process in bench-wrapper.sh will manage
            # the graceful shutdown after Signal 34

        except Exception as e:
            display.error(f"Error during worker signaling: {e}")
            display.warning("Proceeding with restart despite signaling error.")

    resume_called = False # Flag to ensure resume is called only once


    # --- Restart Execution ---
    display.print(f"\n[bold cyan]🔄 Restarting Services[/bold cyan] ({target_desc}, standard stop-then-start)...")

    try:
        # Execute the restart command in parallel using the single restart function
        execute_parallel_command(
            services_to_target,
            util_restart_service, # Use the single restart function from api.py
            action_verb="restarting",
            show_progress=True,
            # Pass all relevant parameters down
            wait=wait,
            # Pass the actual flag value (True, False, or None)
            wait_workers=wait_workers,
            force_kill_timeout=None, # No forced kill in this flow
        )

        # Success/failure summary is now handled within execute_parallel_command

    finally:

        # --- Resume Workers ---
        if suspension_needed and not resume_called:
            display.heading("🟢 Resuming RQ Workers (Redis Flag)")
            try:
                # Call without site parameter
                success = control_rq_workers(action=ActionEnum.resume)

                if not success:
                    display.warning("Failed to resume RQ workers via Redis flag. Check logs above.")
                    display.warning("You may need to manually remove the 'rq:suspended' key in Redis if workers remain suspended.")
                # else: Success message printed by control_rq_workers

            except Exception as e: # Catch unexpected errors *calling* rq_controller_main
                 display.error(f"An unexpected error occurred while trying to call rq_controller to resume workers: {e}")
                 import traceback
                 traceback.print_exc(file=sys.stderr)
            finally:
                 resume_called = True # Mark as attempted regardless of outcome
        # Final message indicating the stop/start try block is done
        display.print(f"\nRestart process complete for {target_desc}.")
    # --- End of Restart Execution ---
