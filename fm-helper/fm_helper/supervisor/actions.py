import time
import signal
from typing import List, Optional, Dict, Any
from xmlrpc.client import Fault

from .constants import STOPPED_STATES, is_worker_process, ProcessStates, get_base_worker_name, DEFAULT_SUFFIXES
from .state import RollingState
from .exceptions import SupervisorOperationFailedError, SupervisorConnectionError
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
    If process_names is None:
      - If 'state' is provided, starts all non-workers and only workers matching that state.
      - If 'state' is None, starts all non-workers and only the currently active worker state.
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
            # --- Case 2: No specific processes requested (Apply Blue/Green or specific state logic) ---
            if state:
                if verbose:
                    print(f"Attempting to start non-workers and [b yellow]{state}[/b yellow] workers in [b magenta]{service_name}[/b magenta]...")
                target_worker_suffix = state
            else:
                if verbose:
                    print(f"Attempting to start non-workers and [b green]active[/b green] workers in [b magenta]{service_name}[/b magenta] (applying Blue/Green logic)...")
                rolling_state = RollingState() # Only needed if state is not provided
                target_worker_suffix = None # Will be determined per group if state is None

            for process_info in all_defined_processes_info:
                current_process_name = process_info['name']

                if is_worker_process(current_process_name):
                    # Determine the suffix we *want* to start for this worker group
                    suffix_to_start: Optional[str] = None
                    if state:
                        # If --state is given, use that directly
                        suffix_to_start = state
                    else:
                        # If --state is not given, find the active one using RollingState
                        # Need base name to check state file
                        base_name = get_base_worker_name(current_process_name, suffixes=rolling_state.get_suffixes()[0] + ',' + rolling_state.get_suffixes()[1])
                        suffix_to_start = rolling_state.get_active_suffix(base_name)

                    # Construct the full name of the worker process we intend to start
                    base_name_for_construct = get_base_worker_name(current_process_name, suffixes=DEFAULT_SUFFIXES)
                    intended_process_name = f"{base_name_for_construct}-{suffix_to_start}"

                    # --- Improved Matching Logic ---
                    process_matches_target = False
                    # Check 1: Does the current name start with the base name?
                    if current_process_name.startswith(base_name_for_construct):
                        # Check 2: Get the part *after* the base name
                        trailer = current_process_name[len(base_name_for_construct):]
                        # Check 3: Does the trailer start with the target suffix using either separator?
                        if trailer.startswith(f"-{suffix_to_start}") or trailer.startswith(f"_{suffix_to_start}"):
                            process_matches_target = True

                    # Only add the worker instance if it matches the target
                    if process_matches_target:
                        if verbose:
                            print(f"  Identified target worker process: [b green]{current_process_name}[/b green]")
                        processes_to_start_explicitly.append(current_process_name)

                    # Optional: Log skipped workers more clearly
                    elif not process_matches_target:
                        # Ensure suffix_to_start is defined for the log message
                        log_suffix = suffix_to_start if suffix_to_start else "active"
                        if verbose:
                            print(f"  Skipping worker process [dim]{current_process_name}[/dim] (doesn't match target state '{log_suffix}')")

                else:
                    # Non-worker process, always add it
                    if verbose:
                        print(f"  Identified non-worker process: [b blue]{current_process_name}[/b blue]")
                    processes_to_start_explicitly.append(current_process_name)

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
    process_names: Optional[List[str]], # Currently unused, restarts all
    wait: bool,
    force_kill_timeout: Optional[int] = None,
    wait_workers: bool = False, # Determines strategy
    suffixes: str = DEFAULT_SUFFIXES,
    rolling_timeout: int = 60
) -> bool:
    """Handle the 'restart' action by performing a stop/start sequence.
    Uses standard stop-then-start if wait_workers=True.
    Uses hybrid rolling restart if wait_workers=False (default).
    """
    action = "restart"
    print(f"Initiating restart for [b magenta]{service_name}[/b magenta]...")

    # --- Get Initial State ---
    try:
        all_initial_info = supervisor_api.getAllProcessInfo()
        initial_process_map = {info['name']: info for info in all_initial_info}
        initial_running_processes = {
            name for name, info in initial_process_map.items()
            if info['state'] == ProcessStates.RUNNING
        }
    except (Fault, SupervisorConnectionError) as e:
        print(f"[red]Error getting initial process list for {service_name}: {e.faultString if isinstance(e, Fault) else str(e)}[/red]")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (restart initial)")
        return False # Cannot proceed without initial state

    # --- Strategy Selection ---
    if wait_workers:
        # --- Standard Restart Strategy ---
        print(f"  Using Standard Restart strategy (--wait-workers).")
        print(f"  Stopping all processes in {service_name}...")
        # Pass None for process_names to stop all
        stop_success = _handle_stop(supervisor_api, service_name, None, wait, force_kill_timeout, wait_workers)

        if not stop_success:
            print(f"[red]Error:[/red] Failed to stop all processes during standard restart of {service_name}. Aborting start.")
            # Raise an error to indicate failure to the parallel executor
            raise SupervisorOperationFailedError("Failed to stop processes during standard restart", service_name=service_name)

        print(f"  Starting previously running processes in {service_name}...")
        # Only start processes that were initially running
        processes_to_start = list(initial_running_processes)
        if not processes_to_start:
            print("  No processes were running initially. Nothing to start.")
            return True # Considered success as stop worked and nothing needed starting

        start_results = _handle_start(supervisor_api, service_name, processes_to_start, wait, verbose=False) # verbose=False for less noise

        if start_results.get("failed"):
            failed_list = ', '.join(start_results["failed"])
            print(f"[red]Error:[/red] Failed to start some processes during standard restart: {failed_list}")
            raise SupervisorOperationFailedError(f"Failed to start processes: {failed_list}", service_name=service_name)

        print(f"[green]Standard restart completed successfully for {service_name}.[/green]")
        return True

    else:
        # --- Hybrid Rolling Restart Strategy ---
        print(f"  Using Hybrid Rolling Restart strategy (default).")
        rolling_state = RollingState(suffixes=suffixes)
        suffix_pair = rolling_state.get_suffixes()
        non_worker_processes = []
        # New structure: {base_name: {index: {suffix: full_name}}}
        worker_processes_by_base_and_index: Dict[str, Dict[str, Dict[str, str]]] = {}

        # Categorize processes
        for name, info in initial_process_map.items():
            if is_worker_process(name):
                # Use the provided suffixes string for get_base_worker_name
                base_name, index_str = get_base_worker_name(name, suffixes) # Get base and index

                # Ensure base_name exists in the outer dict
                if base_name not in worker_processes_by_base_and_index:
                    worker_processes_by_base_and_index[base_name] = {}

                # Use a default index like '0' if none is found (e.g., worker without trailing number)
                # Though supervisor usually adds numbers, handle defensively.
                index_key = index_str if index_str is not None else "0"

                # Ensure index_key exists in the inner dict
                if index_key not in worker_processes_by_base_and_index[base_name]:
                    worker_processes_by_base_and_index[base_name][index_key] = {}

                # --- Robust Suffix Detection ---
                # Extract the part after the base name
                trailer = name[len(base_name):]

                # Check if the trailer starts with the suffix patterns
                detected_suffix = None
                if trailer.startswith(f"-{suffix_pair[0]}") or trailer.startswith(f"_{suffix_pair[0]}"):
                    detected_suffix = suffix_pair[0]
                elif trailer.startswith(f"-{suffix_pair[1]}") or trailer.startswith(f"_{suffix_pair[1]}"):
                    detected_suffix = suffix_pair[1]

                # Store if a suffix was detected, under the correct base_name and index_key
                if detected_suffix:
                    worker_processes_by_base_and_index[base_name][index_key][detected_suffix] = name
                # --- End Robust Suffix Detection ---
            else:
                non_worker_processes.append(name)

        # 1. Restart Non-Workers (Standard Stop/Start)
        if non_worker_processes:
            print(f"  Restarting {len(non_worker_processes)} non-worker process(es)...")
            non_workers_to_stop = [p for p in non_worker_processes if p in initial_running_processes]
            non_workers_to_start = non_workers_to_stop # Start what was running

            if non_workers_to_stop:
                print(f"    Stopping non-workers: {', '.join(non_workers_to_stop)}")
                stop_nw_ok = _handle_stop(supervisor_api, service_name, non_workers_to_stop, wait, force_kill_timeout, wait_workers=True) # Force wait_workers=True for non-workers
                if not stop_nw_ok:
                    raise SupervisorOperationFailedError("Failed to stop non-worker processes", service_name=service_name)

            if non_workers_to_start:
                print(f"    Starting non-workers: {', '.join(non_workers_to_start)}")
                start_nw_results = _handle_start(supervisor_api, service_name, non_workers_to_start, wait, verbose=False)
                if start_nw_results.get("failed"):
                    failed_nw = ', '.join(start_nw_results["failed"])
                    raise SupervisorOperationFailedError(f"Failed to start non-worker processes: {failed_nw}", service_name=service_name)
            else:
                 print("    No running non-workers to stop/start.")
        else:
            print("  No non-worker processes found.")

        # 2. Rolling Restart Workers
        if worker_processes_by_base_and_index:
            print(f"  Performing rolling restart for worker groups...")
            overall_worker_success = True
            # Iterate through each base name (e.g., 'release...-ansible_workers-worker')
            for base_name, index_map in worker_processes_by_base_and_index.items():
                print(f"    Processing worker group: '{base_name}'")
                # Iterate through each index found for this base name (e.g., '0', '1', ...)
                for index, color_map_for_index in index_map.items():
                    pair_identifier = f"{base_name} (Index: {index})" # For logging
                    if len(color_map_for_index) != 2:
                        print(f"    [yellow]Warning:[/yellow] Skipping rolling restart for '{pair_identifier}': Missing one color instance ({color_map_for_index}). Performing standard restart instead.")
                    # Standard restart for this incomplete pair
                    pair_processes = list(color_map_for_index.values())
                    pair_to_stop = [p for p in pair_processes if p in initial_running_processes]
                    pair_to_start = pair_to_stop
                    if pair_to_stop:
                        stop_pair_ok = _handle_stop(supervisor_api, service_name, pair_to_stop, wait, force_kill_timeout, wait_workers=True)
                        if stop_pair_ok and pair_to_start:
                            start_pair_res = _handle_start(supervisor_api, service_name, pair_to_start, wait, verbose=False)
                            if start_pair_res.get("failed"): overall_worker_success = False
                        elif not stop_pair_ok:
                            overall_worker_success = False
                    continue # Move to next worker group

                # --- Rolling Logic for Complete Pair ---
                print(f"      Rolling pair '{pair_identifier}'...")
                active_suffix = rolling_state.get_active_suffix(base_name)
                inactive_suffix = rolling_state.get_inactive_suffix(base_name)
                active_process_name = color_map_for_index[active_suffix]
                inactive_process_name = color_map_for_index[inactive_suffix]

                # a. Start Inactive
                print(f"      Starting inactive instance: {inactive_process_name} ({inactive_suffix})")
                # Use wait=False for API call, then explicit wait below
                # Remove the redundant 'state' parameter as we are specifying the process name directly
                start_inactive_results = _handle_start(supervisor_api, service_name, [inactive_process_name], wait=False, verbose=False)
                if start_inactive_results.get("failed"):
                    print(f"      [red]Failed to initiate start for {inactive_process_name}. Aborting roll for '{pair_identifier}'.[/red]")
                    overall_worker_success = False
                    continue

                # b. Wait for Inactive to be RUNNING
                print(f"      Waiting for {inactive_process_name} to become RUNNING (timeout: {rolling_timeout}s)...")
                wait_start_ok = _wait_for_processes_start(supervisor_api, service_name, [inactive_process_name], rolling_timeout)
                if not wait_start_ok:
                    print(f"      [red]Inactive instance {inactive_process_name} failed to become RUNNING. Aborting roll for '{pair_identifier}'. Attempting cleanup...[/red]")
                    # Attempt to stop the instance we tried to start
                    _handle_stop(supervisor_api, service_name, [inactive_process_name], wait=True, force_kill_timeout=5, wait_workers=True)
                    overall_worker_success = False
                    continue

                # c. Switch Active Suffix
                print(f"      Switching active state for group '{base_name}' to '{inactive_suffix}'...")
                if not rolling_state.set_active_suffix(base_name, inactive_suffix):
                    print(f"      [red]Failed to update state file for '{base_name}'. Aborting roll. Manual cleanup may be needed.[/red]")
                    overall_worker_success = False
                    continue # State is inconsistent, stop here

                # d. Stop Old Active
                print(f"      Stopping previously active instance: {active_process_name} ({active_suffix})")
                # Use wait_workers=True here for explicit stop
                stop_old_active_ok = _handle_stop(supervisor_api, service_name, [active_process_name], wait, force_kill_timeout, wait_workers=True)
                if not stop_old_active_ok:
                    print(f"      [yellow]Warning:[/yellow] Failed to cleanly stop old active instance {active_process_name}.")
                    # Don't necessarily fail the whole restart, but log it.
                    # overall_worker_success = False # Optional: make this a failure condition

                print(f"      Rolling for pair '{pair_identifier}' completed.")

            if not overall_worker_success:
                 raise SupervisorOperationFailedError("One or more worker groups failed during rolling restart.", service_name=service_name)

        else:
            print("  No worker processes found for rolling restart.")

        print(f"[green]Hybrid rolling restart completed successfully for {service_name}.[/green]")
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

    for process_name in process_names:
        try:
            # Pass signal name directly, supervisor should handle it
            print(f"  Signaling process [green]{process_name}[/green]...")
            supervisor_api.signalProcess(process_name, signal_name.upper())
            results[process_name] = True
        except Fault as e:
            fault_string = getattr(e, 'faultString', '')
            if "BAD_NAME" in fault_string:
                print(f"  [yellow]Warning:[/yellow] Process [green]{process_name}[/green] not found (BAD_NAME). Skipping signal.")
                results[process_name] = True # Treat as success (process gone)
            elif "NOT_RUNNING" in fault_string:
                print(f"  [yellow]Warning:[/yellow] Process [green]{process_name}[/green] is not running (NOT_RUNNING). Skipping signal.")
                results[process_name] = True # Treat as success (process stopped)
            else:
                print(f"  [red]Error signaling process {process_name}: {e.faultString}[/red]")
                _raise_exception_from_fault(e, service_name, action, process_name)
                results[process_name] = False
        except Exception as e:
            print(f"  [red]Unexpected error signaling process {process_name}: {e}[/red]")
            results[process_name] = False

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
