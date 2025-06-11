import os
import subprocess
import sys
import time
from typing import Annotated, Optional, List

import typer

from ..supervisor.constants import STOPPED_STATES
from ..rq_controller import control_rq_workers, check_rq_suspension, wait_for_rq_workers_suspended, ActionEnum
from ..display import DisplayManager
from ..command_utils import validate_services, get_process_description
from ..cli import ServiceNameEnumFactory, execute_parallel_command, get_service_names_for_completion
from ..supervisor.api import restart_service as util_restart_service, signal_service_workers as util_signal_service_workers, stop_service as util_stop_service, start_service as util_start_service


command_name = "restart"

ServiceNamesEnum = ServiceNameEnumFactory()


def _suspend_rq_workers(display: DisplayManager, wait_workers: Optional[bool], wait_workers_timeout: int, wait_workers_poll: int, wait_workers_verbose: bool) -> bool:
    """Suspend RQ workers via Redis flag and optionally wait for completion.
    
    Logic:
    1. Sets 'rq:suspended' flag in Redis using control_rq_workers
    2. Verifies the flag was set correctly using check_rq_suspension  
    3. If wait_workers=True: waits for workers to reach suspended state
    4. Returns success/failure status for the entire suspension process
    
    Returns:
        True if suspension completed successfully, False to abort restart
    """
    display.print("⏸️  Suspending RQ workers via Redis flag...")
    try:
        success = control_rq_workers(action=ActionEnum.suspend)

        if not success:
            display.error("Failed to suspend RQ workers via Redis.", exit_code=1)
            display.print("Check logs above for details from rq_controller.")
            display.print("Aborting restart.")
            return False
        else:
            display.success("RQ workers suspended via Redis flag.")

            display.dimmed("Verifying suspension status...")
            suspension_status = check_rq_suspension()

            if suspension_status is True:
                display.success("Verification successful: RQ suspension flag is set in Redis.")
            elif suspension_status is False:
                display.error("Verification failed: RQ suspension flag was NOT found in Redis after attempting to set it.")
                display.print("Aborting restart.")
                return False
            else:
                display.error("Could not verify suspension status due to an error during the check.")
                display.print("Check logs above for details from rq_controller check.")
                display.print("Aborting restart.")
                return False

            if wait_workers is True:
                display.print("\n[cyan]Waiting for RQ workers to complete their current jobs...[/cyan]")
                if not wait_for_rq_workers_suspended(
                    timeout=wait_workers_timeout,
                    poll_interval=wait_workers_poll,
                    verbose=wait_workers_verbose
                ):
                    display.error("Workers did not become idle within the timeout period.")
                    display.print("Aborting restart to avoid interrupting jobs.")
                    control_rq_workers(action=ActionEnum.resume)
                    return False

    except Exception as e:
        display.error(f"An unexpected error occurred during worker suspension or verification: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


def _signal_workers_for_graceful_shutdown(display: DisplayManager, services_to_target: List[str]):
    """Signal workers for graceful shutdown without waiting.
    
    Logic:
    1. Iterates through all target services
    2. Uses util_signal_service_workers to send graceful exit signals
    3. Logs which workers were signaled in each service
    4. Designed to work with bench-wrapper.sh monitor process
    """
    display.print("Signaling workers for graceful shutdown...")
    try:
        for service_name in services_to_target:
            signaled_workers = util_signal_service_workers(service_name)
            if signaled_workers:
                display.success(f"Signaled workers in {display.highlight(service_name)}: {', '.join(signaled_workers)}")

    except Exception as e:
        display.error(f"Error during worker signaling: {e}")
        display.warning("Proceeding with restart despite signaling error.")


def _resume_rq_workers(display: DisplayManager) -> bool:
    """Resume RQ workers by removing Redis suspension flag.
    
    Logic:
    1. Calls control_rq_workers with ActionEnum.resume
    2. Handles both success and failure cases gracefully
    3. Provides user feedback about resume status
    4. Used in finally block to ensure cleanup
    
    Returns:
        True if resume succeeded, False if failed (non-fatal)
    """
    display.print("▶️  Resuming RQ workers via Redis flag...")
    try:
        success = control_rq_workers(action=ActionEnum.resume)

        if not success:
            display.warning("Failed to resume RQ workers via Redis flag. Check logs above.")
            display.warning("You may need to manually remove the 'rq:suspended' key in Redis if workers remain suspended.")
            return False

    except Exception as e:
         display.error(f"An unexpected error occurred while trying to call rq_controller to resume workers: {e}")
         import traceback
         traceback.print_exc(file=sys.stderr)
         return False
    
    return True

def _run_migration(display: DisplayManager, migrate_timeout: int) -> bool:
    """Run bench migrate with timeout and real-time output.
    
    Logic:
    1. Executes 'bench migrate' from /workspace/frappe-bench directory
    2. Shows real-time output during migration
    3. Applies specified timeout to prevent hanging
    4. Returns success/failure status
    
    Returns:
        True if migration succeeded, False to abort restart
    """
    display.print("🔄 Running bench migrate...")
    display.dimmed(f"Migration timeout: {migrate_timeout}s")
    
    try:
        start_time = time.time()
        
        # Use Popen for real-time output streaming
        process = subprocess.Popen(
            ["bench", "migrate"],
            cwd="/workspace/frappe-bench",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Combine stderr with stdout
            text=True,
            bufsize=0,  # Unbuffered for immediate output
            universal_newlines=True,
            env={"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", **dict(os.environ)}
        )
        
        # Stream output in real-time
        output_lines = []
        try:
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    # Clean the line of control characters
                    line = output.rstrip('\r\n')
                    if line:  # Only show non-empty lines
                        # Use print directly to avoid Rich formatting issues
                        print(f"  {line}", flush=True)
                        output_lines.append(line)
                
                # Check timeout
                if time.time() - start_time > migrate_timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise subprocess.TimeoutExpired(["bench", "migrate"], migrate_timeout)
        
        finally:
            # Ensure process is cleaned up
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        
        elapsed_time = time.time() - start_time
        return_code = process.returncode
        
        if return_code == 0:
            display.success(f"Migration completed successfully in {elapsed_time:.1f}s")
            return True
        else:
            display.error(f"Migration failed with exit code {return_code}")
            if output_lines:
                display.print("Recent migration output:")
                for line in output_lines[-10:]:  # Show last 10 lines
                    print(f"  {line}")
            return False
            
    except subprocess.TimeoutExpired:
        display.error(f"Migration timed out after {migrate_timeout}s")
        display.print("Consider increasing --migrate-timeout if migration needs more time.")
        return False
    except FileNotFoundError:
        display.error("bench command not found. Ensure you're running from within the Frappe environment.")
        return False
    except Exception as e:
        display.error(f"Unexpected error during migration: {e}")
        return False

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
    migrate: Annotated[
        bool,
        typer.Option(
            "--migrate",
            help="Run 'bench migrate' after stopping services but before starting them.",
        )
    ] = False,
    migrate_timeout: Annotated[
        int,
        typer.Option(
            "--migrate-timeout",
            help="Timeout (seconds) for bench migrate operation (default: 300).",
        )
    ] = 300,
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
    force_kill_timeout: Annotated[
        Optional[int],
        typer.Option(
            "--force-kill-timeout",
            help="Timeout (seconds) after which stubborn non-worker processes will be forcefully killed during restart.",
        )
    ] = None,
    wait_after_signal_timeout: Annotated[
        int,
        typer.Option(
            "--wait-after-signal-timeout",
            help="Timeout (seconds) for waiting after signaling workers (default: 60).",
        )
    ] = 60,
):
    """Restart services with optional RQ worker coordination and migration.
    
    Performs supervisor-based restart with optional Redis worker suspension.
    Can run bench migrate between stop and start phases.
    Can wait for workers to complete jobs or signal them for graceful shutdown.
    Always attempts to resume workers after restart completion.
    """
    display: DisplayManager = ctx.obj['display']

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    valid, target_desc = validate_services(display, services_to_target, all_services, "restart")
    if not valid:
        return
    
    # Add wait information to the message
    wait_desc = "(with wait)" if wait else "(without wait)"
    display.print(f"\nAttempting to restart {target_desc} {wait_desc}...")

    suspension_needed = suspend_rq or (wait_workers is True)
    if suspension_needed:
        if not _suspend_rq_workers(display, wait_workers, wait_workers_timeout, wait_workers_poll, wait_workers_verbose):
            raise typer.Exit(code=1)

    if wait_workers is False:
        _signal_workers_for_graceful_shutdown(display, services_to_target)

    resume_called = False
    try:
        # First, stop all services
        execute_parallel_command(
            services_to_target,
            util_stop_service,
            action_verb="stopping",
            show_progress=True,
            process_name_list=None,  # Stop all processes for restart
            wait=wait,
            wait_workers=wait_workers,
            force_kill_timeout=force_kill_timeout,
        )
        
        # Run migration if requested
        if migrate:
            if not _run_migration(display, migrate_timeout):
                display.error("Migration failed. Aborting restart - services remain stopped.")
                raise typer.Exit(code=1)
        
        # Then start all services
        execute_parallel_command(
            services_to_target,
            util_start_service,
            action_verb="starting",
            show_progress=True,
            process_name_list=None,  # Start all defined processes
            wait=wait,
        )
        
    finally:
        if suspension_needed and not resume_called:
            _resume_rq_workers(display)
            resume_called = True
        
        display.print("\nRestart sequence complete.")
