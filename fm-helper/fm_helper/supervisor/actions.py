from typing import Optional, List, Any, Dict
from xmlrpc.client import Fault

from rich import print

from .constants import is_worker_process
from .fault_handler import _raise_exception_from_fault
from .stop_helpers import (
    _stop_single_process_with_logic,
    _wait_for_worker_processes_stop
)


def _handle_stop(
    supervisor_api,
    service_name: str,
    process_names: Optional[List[str]],
    wait: bool,
    force_kill_timeout: Optional[int],
    wait_workers: bool
) -> bool:
    """Handle the 'stop' action by iterating through target processes and applying wait logic."""
    action = "stop"
    results = {}
    target_process_names: List[str] = []
    process_info_map: Dict[str, Dict[str, Any]] = {}

    # --- Get All Process Info Once ---
    try:
        all_info = supervisor_api.getAllProcessInfo()
        if not all_info:
            print(f"No processes found running in [b magenta]{service_name}[/b magenta].")
            return True # Nothing to stop
        # Populate the process info map
        process_info_map = {info['name']: info for info in all_info}
    except Fault as e:
        print(f"[red]Error getting process list for {service_name}: {e.faultString}[/red]")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (stop)")
        return False

    # --- Determine Target Processes ---
    if process_names:
        # Validate specified process names against what's actually running
        target_process_names = []
        missing_names = []
        for name in process_names:
            if name in process_info_map:
                target_process_names.append(name)
            else:
                missing_names.append(name)
        
        if missing_names:
            print(f"[yellow]Warning:[/yellow] Specified process(es) not found or not running: {', '.join(missing_names)}")
        if not target_process_names:
            print(f"[red]Error:[/red] None of the specified processes are currently running.")
            return False
        print(f"Preparing to stop specific process(es): [b yellow]{', '.join(target_process_names)}[/b yellow] in [b magenta]{service_name}[/b magenta]...")
    else:
        # Use all running processes
        target_process_names = list(process_info_map.keys())
        print(f"Preparing to stop all processes in [b magenta]{service_name}[/b magenta]...")

    # --- Determine Wait Override ---
    # --no-wait-workers means wait_workers is False.
    # We want to override the main 'wait' ONLY if wait=True AND wait_workers=False
    apply_worker_no_wait_override = wait and not wait_workers

    if apply_worker_no_wait_override:
        print("[dim]--no-wait-workers specified: Stop calls for worker processes will not wait, even if --wait is active.[/dim]")

    # --- Iterate and Stop Each Process ---
    for process_name in target_process_names:
        try:
            # Determine the effective wait for *this specific process*
            effective_wait_for_this_process = wait # Start with the global wait flag

            is_worker = is_worker_process(process_name)

            # Apply override if: global wait is True, --no-wait-workers is active, AND this is a worker
            if is_worker and apply_worker_no_wait_override:
                effective_wait_for_this_process = False

            # Call the single process handler with the calculated wait and process info
            # Note: wait_workers flag is still passed for the force_kill_timeout logic inside the helper
            results[process_name] = _stop_single_process_with_logic(
                supervisor_api,
                service_name,
                process_name,
                wait=effective_wait_for_this_process, # Use calculated wait for API call
                force_kill_timeout=force_kill_timeout,
                wait_workers=wait_workers, # Pass original flag for internal checks
                process_info=process_info_map.get(process_name)
            )
        except Fault as e:
            # Catch faults raised by _stop_single_process_with_logic or its sub-helpers
            # _raise_exception_from_fault is already called inside the helper for specific faults
            # This catch is for unexpected faults during the helper execution itself
            print(f"[red]Error during stop operation for process {process_name}: {e.faultString}[/red]")
            # Ensure _raise_exception_from_fault is called if not already handled
            # This might be redundant if helpers always call it, but acts as a safety net.
            try:
                _raise_exception_from_fault(e, service_name, action, process_name)
            except Exception: # Catch the exception raised by _raise_exception_from_fault
                pass # Exception is raised, loop continues or function exits
            results[process_name] = False # Mark as failed if exception occurred
        except Exception as e: # Catch non-Fault errors
            print(f"[red]Unexpected error stopping process {process_name}: {e}[/red]")
            results[process_name] = False

    # --- Return overall success ---
    return all(results.values())
    # Return True only if all individual stop operations succeeded
    return all(results.values())

# --- Remove the _stop_all_processes_with_logic function ---
# It's no longer needed as _handle_stop now iterates individually when necessary.
# Make sure to delete the entire function definition for _stop_all_processes_with_logic.


# --- Helper: Start Action ---
def _handle_start(supervisor_api, service_name: str, process_names: Optional[List[str]], wait: bool) -> bool:
    """Handle the 'start' action."""
    action = "start"
    try:
        if process_names:
            results = {}
            for process in process_names:
                # startProcess returns True on success, raises Fault on failure
                supervisor_api.startProcess(process, wait)
                print(f"Started process [b green]{process}[/b green] in [b magenta]{service_name}[/b magenta]")
                results[process] = True
            return all(results.values())
        else:
            # startAllProcesses raises Fault on failure for any process
            supervisor_api.startAllProcesses(wait)
            print(f"Started all processes in [b magenta]{service_name}[/b magenta]")
            return True # If no Fault was raised, assume success
    except Fault as e:
        _raise_exception_from_fault(e, service_name, action, process_names[0] if process_names else None)


# --- Helper: Restart Action ---
def _handle_restart(
    supervisor_api, 
    service_name: str,
    process_names: Optional[List[str]], 
    wait: bool,
    force_kill_timeout: Optional[int] = None,
    wait_workers: bool = False
) -> bool:
    """Handle the 'restart' action by performing a stop/start sequence."""
    print(f"[b blue]{service_name}[/b blue] - Stopping all processes...")
    # Use internal call to stop, respecting all parameters. Can raise exceptions.
    _handle_stop(supervisor_api, service_name, process_names, wait, force_kill_timeout, wait_workers)
    # If stop succeeded (no exception), proceed to start
    print(f"[b blue]{service_name}[/b blue] - Starting all processes...")
    # Use internal call to start, respecting 'wait'. Can raise exceptions.
    _handle_start(supervisor_api, service_name, process_names, wait) # Use helper
    print(f"Gracefully restarted all processes in [b magenta]{service_name}[/b magenta]")
    return True # Graceful restart sequence completed without exceptions


# --- Helper: Info Action ---
def _handle_info(supervisor_api, service_name: str) -> List[Dict[str, Any]]:
    """Handle the 'info' action."""
    action = "info"
    try:
        # getAllProcessInfo can raise Faults (e.g., SHUTDOWN_STATE)
        return supervisor_api.getAllProcessInfo()
    except Fault as e:
        _raise_exception_from_fault(e, service_name, action)
def _wait_for_worker_processes_stop(supervisor_api, service_name: str, timeout: int) -> bool:
    """Wait specifically for worker processes to reach a stopped state."""
    worker_process_names = []
    try:
        all_info = supervisor_api.getAllProcessInfo()
        # Store process info for each process
        process_info_map = {info['name']: info for info in all_info}
        # Identify worker process names
        worker_process_names = [info['name'] for info in all_info if is_worker_process(info['name'])]
    except Fault as e:
        print(f"  [red]Error getting process info to identify workers:[/red] {e.faultString}")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (worker wait identify)")
        return False  # Indicate failure to even identify workers

    if not worker_process_names:
        print("  No worker processes found to wait for.")
        return True  # No workers means the condition is met

    num_workers = len(worker_process_names)
    worker_names_str = ", ".join(f"[b green]{name}[/b green]" for name in worker_process_names)
    print(f"  Waiting up to {timeout}s for {num_workers} worker process(es) ({worker_names_str}) to stop...")

    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        all_workers_stopped_this_check = True
        try:
            # Re-fetch info in each loop iteration
            current_all_info = supervisor_api.getAllProcessInfo()
            # Create a dict for quick lookup of current states
            current_states = {info['name']: info['state'] for info in current_all_info}

            for worker_name in worker_process_names:
                current_state = current_states.get(worker_name)
                # If worker process is missing from current info OR its state is NOT stopped
                if current_state is None or current_state not in STOPPED_STATES:
                    all_workers_stopped_this_check = False
                    break  # No need to check other workers in this iteration

            if all_workers_stopped_this_check:
                print(f"  All {num_workers} identified worker process(es) stopped gracefully.")
                return True

        except Fault as e:
            # If supervisor is shutting down during wait, consider it done.
            if "SHUTDOWN_STATE" in e.faultString:
                print(f"  Supervisor in [b magenta]{service_name}[/b magenta] is shutting down, assuming workers stopped.")
                return True
            # Handle other potential faults during getAllProcessInfo
            print(f"  [red]Error checking worker status:[/red] {e.faultString}")
            # Don't raise here, just log and assume not stopped for this check
            all_workers_stopped_this_check = False

        # Wait only if not all workers were stopped in this check
        if not all_workers_stopped_this_check:
            time.sleep(0.5)

    print(f"  [yellow]Timeout reached.[/yellow] Not all identified worker processes stopped gracefully.")
    return False
