import time
import signal
from typing import List, Optional, Dict, Any
from xmlrpc.client import Fault

from .constants import STOPPED_STATES, is_worker_process, ProcessStates
from .exceptions import SupervisorOperationFailedError, SupervisorConnectionError
from .fault_handler import _raise_exception_from_fault
from .stop_helpers import (
    _stop_single_process_with_logic,
    _wait_for_worker_processes_stop
)
from rich import print


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
    results: Dict[str, bool] = {} # Store success/failure per process
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
        return {"started": [], "already_running": [], "failed": ["<unexpected error>"]}

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
def _handle_start(supervisor_api, service_name: str, process_names: Optional[List[str]], wait: bool, state: Optional[str] = None, verbose: bool = False) -> Dict[str, List[str]]:
    """
    Handle the 'start' action.

    If specific process_names are given, starts only those.
    If process_names is None, starts all defined processes.
    The 'state' parameter is ignored when starting all processes.
    """
    action = "start"
    processes_to_start_explicitly: List[str] = []
    # Initialize detailed results dictionary at the beginning
    start_results = {"started": [], "already_running": [], "failed": []}

    try:
        # Get info for ALL processes defined in this supervisor instance
        all_defined_processes_info = supervisor_api.getAllProcessInfo()
        if not all_defined_processes_info:
            print(f"No processes defined or found in [b magenta]{service_name}[/b magenta]. Nothing to start.")
            return {"started": [], "already_running": [], "failed": []}

        defined_process_names = {info['name'] for info in all_defined_processes_info}

        if process_names:
            # --- Case 1: Specific processes requested ---
            if verbose:
                print(f"Attempting to start specific process(es) in [b magenta]{service_name}[/b magenta]: {', '.join(process_names)}")
            missing_names = [name for name in process_names if name not in defined_process_names]
            if missing_names:
                print(f"[yellow]Warning:[/yellow] Specified process(es) not defined in supervisor config: {', '.join(missing_names)}")

            processes_to_start_explicitly = [name for name in process_names if name in defined_process_names]
            if not processes_to_start_explicitly:
                 print("[red]Error:[/red] None of the specified processes are defined. Nothing to start.")
                 return {"started": [], "already_running": [], "failed": process_names}  # Return failed list with requested names

        else:
            # --- Case 2: No specific processes requested -> Start ALL defined ---
            if verbose:
                print(f"Attempting to start all defined processes in [b magenta]{service_name}[/b magenta]...")
            processes_to_start_explicitly = list(defined_process_names)
            if not processes_to_start_explicitly:
                 print("  No processes defined to start.")
                 return start_results # Nothing to start is not an error

            if not processes_to_start_explicitly:
                 print("  No suitable processes found to start (check worker state files?).")
                 return {"started": [], "already_running": [], "failed": []}  # Nothing to start is not an error

        # --- Execute Start for the determined list ---
        if verbose:
            print(f"Final list of processes to start: {', '.join(processes_to_start_explicitly)}")

        # Create a map of name -> info for easy lookup
        process_info_map = {info['name']: info for info in all_defined_processes_info}

        for process_name_to_start in processes_to_start_explicitly:
            try:
                # Get the full info for the process we intend to start
                process_info = process_info_map.get(process_name_to_start)
                if not process_info:
                    # Should not happen if list was built correctly, but safety check
                    print(f"[yellow]Warning:[/yellow] Skipping {process_name_to_start} - info not found.")
                    results[process_name_to_start] = False # Mark as failed
                    continue

                # Determine the name to use for the API call (group:name or just name)
                group_name = process_info.get('group')
                name_for_api = process_name_to_start
                if group_name and not process_name_to_start.startswith(f"{group_name}:"):
                    name_for_api = f"{group_name}:{process_name_to_start}"

                # Call startProcess with the potentially prefixed name
                supervisor_api.startProcess(name_for_api, wait)
                # Add to 'started' list on success
                start_results["started"].append(process_name_to_start)
            except Fault as start_fault:
                fault_string = getattr(start_fault, 'faultString', '')
                if "ALREADY_STARTED" in fault_string:
                    # Add to 'already_running' list
                    start_results["already_running"].append(process_name_to_start)
                elif "FATAL" in fault_string:
                     print(f"  [red]Error:[/red] Process [b green]{process_name_to_start}[/b green] entered FATAL state during start.")
                     # Add to 'failed' list
                     start_results["failed"].append(process_name_to_start)
                     _raise_exception_from_fault(start_fault, service_name, action, process_name_to_start)
                elif "BAD_NAME" in fault_string:
                     print(f"  [red]Error:[/red] Process [b green]{process_name_to_start}[/b green] not found by supervisor (BAD_NAME).")
                     # Add to 'failed' list
                     start_results["failed"].append(process_name_to_start)
                     _raise_exception_from_fault(start_fault, service_name, action, process_name_to_start)
                else:
                    # Add to 'failed' list for other faults
                    start_results["failed"].append(process_name_to_start)
                    _raise_exception_from_fault(start_fault, service_name, action, process_name_to_start)

        # Return the detailed results dictionary
        return start_results

    except Fault as e:
        # Catch faults during initial getAllProcessInfo or unexpected issues
        _raise_exception_from_fault(e, service_name, action, process_names[0] if process_names else None)
        # Return failure state if exception occurs before starting loop
        return {"started": [], "already_running": [], "failed": processes_to_start_explicitly or ["<error getting process list>"]}
    except Exception as e: # Catch other unexpected errors
        print(f"[red]Unexpected error during start action for {service_name}: {e}[/red]")
        return {"started": [], "already_running": [], "failed": ["<unexpected error>"]}


# --- Helper: Restart Action ---
# --- Helper: Wait for Start ---
def _wait_for_processes_start(supervisor_api, service_name: str, process_names: List[str], timeout: int) -> bool:
    """Wait for specific processes to reach RUNNING state."""
    if not process_names:
        return True # Nothing to wait for

    print(f"  Waiting up to {timeout}s for {len(process_names)} process(es) to reach RUNNING state...")
    start_time = time.monotonic()
    process_set = set(process_names)
    running_set = set()

    while time.monotonic() - start_time < timeout:
        all_running_this_check = True
        try:
            all_info = supervisor_api.getAllProcessInfo()
            current_states = {info['name']: info['state'] for info in all_info}

            for name in process_set:
                if name in running_set: # Already confirmed running in a previous check
                    continue

                current_state = current_states.get(name)
                if current_state == ProcessStates.RUNNING:
                    running_set.add(name) # Mark as running
                elif current_state in (ProcessStates.FATAL, ProcessStates.EXITED, ProcessStates.STOPPED, ProcessStates.UNKNOWN):
                    print(f"  [red]Error:[/red] Process [b yellow]{name}[/b yellow] entered non-running state ({ProcessStates(current_state).name}) while waiting for start.")
                    return False # Failure
                else:
                    # Still STARTING, BACKOFF, STOPPING (unexpected but possible race), or not found yet
                    all_running_this_check = False

            if running_set == process_set:
                print(f"  All {len(process_names)} target process(es) are RUNNING.")
                return True

        except Fault as e:
            # If supervisor is shutting down during wait, consider it failed.
            if "SHUTDOWN_STATE" in e.faultString:
                print(f"  [red]Error:[/red] Supervisor in [b magenta]{service_name}[/b magenta] shut down while waiting for processes to start.")
                return False
            # Handle other potential faults during getAllProcessInfo
            print(f"  [red]Error checking process status during start wait:[/red] {e.faultString}")
            # Don't raise here, just log and assume not running for this check
            all_running_this_check = False
        except Exception as e:
             print(f"  [red]Unexpected error checking process status during start wait:[/red] {e}")
             all_running_this_check = False


        if not all_running_this_check:
            time.sleep(1) # Check every second

    print(f"  [yellow]Timeout reached.[/yellow] Not all target processes reached RUNNING state.")
    # List missing processes
    missing_processes = process_set - running_set
    if missing_processes:
         print(f"  Processes not confirmed RUNNING: {', '.join(missing_processes)}")
    return False


def _handle_restart(
    supervisor_api,
    service_name: str,
    process_names: Optional[List[str]], # Still unused, always restarts all
    wait: bool,
    force_kill_timeout: Optional[int] = None,
    wait_workers: bool = False # Keep for consistency, but only standard path is used
) -> bool:
    """Handle the 'restart' action by performing a standard stop-then-start sequence."""
    action = "restart"
    print(f"Initiating restart for [b magenta]{service_name}[/b magenta]...")


    # --- Standard Restart Strategy (Only strategy now) ---
    # The wait_workers flag is implicitly handled by _handle_stop
    print(f"  Using Standard Restart strategy.") # Simplified message
    print(f"  Stopping all processes in {service_name}...")
    # Pass None for process_names to stop all
    # Pass wait_workers=True to ensure worker stop wait logic is used if needed by stop helpers
    stop_success = _handle_stop(supervisor_api, service_name, None, wait, force_kill_timeout, wait_workers=True) # Force wait_workers=True for standard stop

    if not stop_success:
        print(f"[red]Error:[/red] Failed to stop all processes during standard restart of {service_name}. Aborting start.")
        raise SupervisorOperationFailedError("Failed to stop processes during standard restart", service_name=service_name)

    print(f"  Starting all defined processes in {service_name}...")
    # Call _handle_start with None to start all defined processes
    start_results = _handle_start(supervisor_api, service_name, None, wait, verbose=False)

    # _handle_start now raises SupervisorOperationFailedError on failure
    # No need to check start_results["failed"] here, exception handling covers it

    print(f"[green]Standard restart completed successfully for {service_name}.[/green]")
    return True


# --- Helper: Info Action ---
def _handle_signal(supervisor_api, service_name: str, process_names: List[str], signal_name: str) -> bool:
    """Handle the 'signal' action."""
    action = "signal"
    results = {}
    signal_enum = getattr(signal, f"SIG{signal_name.upper()}", None)

    if signal_enum is None:
        print(f"[red]Error:[/red] Invalid signal name '{signal_name}' for service {service_name}.")
        return False

    print(f"Sending signal {signal_name} ({int(signal_enum)}) to {len(process_names)} process(es) in [magenta]{service_name}[/magenta]...")

    if not process_names:
        print("  No specific processes provided to signal.")
        return True # Nothing to do

    # --- Get All Process Info Once ---
    try:
        all_info = supervisor_api.getAllProcessInfo()
        if not all_info:
            print(f"No processes found running in [b magenta]{service_name}[/b magenta]. Cannot send signal.")
            return True # Nothing to signal if no processes exist
        # Create a map of simple name -> full info dict
        process_info_map = {info['name']: info for info in all_info}
    except Fault as e:
        print(f"[red]Error getting process list for {service_name} before signaling: {e.faultString}[/red]")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (signal)")
        return False # Indicate failure if we can't get the process list
    except Exception as e: # Catch other unexpected errors
        print(f"[red]Unexpected error getting process list for {service_name} before signaling: {e}[/red]")
        return False
    # --- End Get All Process Info ---

    for requested_name in process_names:
        # --- Look up info and construct API name ---
        process_info = process_info_map.get(requested_name)

        if not process_info:
            print(f"  [yellow]Warning:[/yellow] Process [b green]{requested_name}[/b green] not found or not running in {service_name}. Skipping signal.")
            results[requested_name] = True # Treat missing process as success for signaling
            continue # Move to the next requested name

        group_name = process_info.get('group')
        name_for_api = requested_name # Default to simple name

        # Construct 'group:name' format if needed
        if group_name and not requested_name.startswith(f"{group_name}:"):
            name_for_api = f"{group_name}:{requested_name}"
        # --- End Look up info ---

        try:
            # Use name_for_api in the call
            supervisor_api.signalProcess(name_for_api, signal_name.upper())
            results[requested_name] = True # Use requested_name as key for results
        except Fault as e:
            fault_string = getattr(e, 'faultString', '')
            # Use requested_name in messages
            if "BAD_NAME" in fault_string:
                print(f"  [dim]Process {requested_name} not found by supervisor (BAD_NAME). Skipping signal.[/dim]")
                results[requested_name] = True # Treat as success (process gone)
            elif "NOT_RUNNING" in fault_string:
                print(f"  [dim]Process {requested_name} not running (NOT_RUNNING). Skipping signal.[/dim]")
                results[requested_name] = True # Treat as success (process stopped)
            else:
                print(f"  [red]Error signaling process {requested_name}: {e.faultString}[/red]")
                # Pass requested_name to the fault handler
                _raise_exception_from_fault(e, service_name, action, requested_name)
                results[requested_name] = False
        except Exception as e:
            # Use requested_name in messages
            print(f"  [red]Unexpected error signaling process {requested_name}: {e}[/red]")
            results[requested_name] = False

    return all(results.values())

def _handle_info(supervisor_api, service_name: str) -> List[Dict[str, Any]]:
    """Handle the 'info' action. Returns raw process info list."""
    action = "info"
    try:
        # getAllProcessInfo can raise Faults (e.g., SHUTDOWN_STATE)
        info_list = supervisor_api.getAllProcessInfo()
        # Return the raw list, formatting happens later if needed
        return info_list if isinstance(info_list, list) else []
    except Fault as e:
        _raise_exception_from_fault(e, service_name, action)
