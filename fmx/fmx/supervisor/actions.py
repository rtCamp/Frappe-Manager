import time
import signal
from typing import List, Optional, Dict, Any
from xmlrpc.client import Fault

from .constants import STOPPED_STATES, is_worker_process, ProcessStates, SIGNAL_NUM_WORKER_GRACEFUL_EXIT, WORKER_PROCESS_IDENTIFIERS
from .exceptions import SupervisorOperationFailedError, SupervisorConnectionError, SupervisorProcessError
from ..display import display
from .fault_handler import _raise_exception_from_fault
from .stop_helpers import (
    _stop_single_process_with_logic,
    _wait_for_worker_processes_stop
)
from ..display import display


def _handle_stop(
    supervisor_api,
    service_name: str,
    process_names: Optional[List[str]],
    wait: bool,
    force_kill_timeout: Optional[int],
    wait_workers: Optional[bool],
    called_from_restart: bool = False,
    verbose: bool = False
) -> Dict[str, List[str]]:
    """Handle the 'stop' action by iterating through target processes and applying wait logic."""
    action = "stop"
    target_process_names: List[str] = []
    process_info_map: Dict[str, Dict[str, Any]] = {}
    
    # Initialize detailed results like start does
    stop_results = {"stopped": [], "already_stopped": [], "failed": []}

    # --- Get All Process Info Once ---
    try:
        all_info = supervisor_api.getAllProcessInfo()
        if not all_info:
            display.print(f"No processes found running in {display.highlight(service_name)}.")
            return stop_results
        # Populate the process info map
        process_info_map = {info['name']: info for info in all_info}
    except Fault as e:
        display.error(f"Error getting process list for {service_name}: {e.faultString}")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (stop)")
        return {"stopped": [], "already_stopped": [], "failed": ["<unexpected error>"]}

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
            display.warning(f"Specified process(es) not found or not running: {', '.join(missing_names)}")
        if not target_process_names:
            display.error("None of the specified processes are currently running.")
            return stop_results
        if verbose:
            display.print(f"Preparing to stop specific process(es): {display.highlight(', '.join(target_process_names))} in {display.highlight(service_name)}...")
    else:
        # Use all running processes
        target_process_names = list(process_info_map.keys())
        if verbose:
            display.print(f"Preparing to stop all processes in {display.highlight(service_name)}...")

    # --- Print Wait Behavior Message (only if NOT called from restart) ---
    if not called_from_restart and verbose:
        if wait_workers is True:
            display.dimmed("Stop calls for worker processes WILL wait for graceful shutdown.")
        elif wait_workers is False:
            display.dimmed("Stop calls for worker processes will not stop it and not wait for graceful shutdown.")

    # --- Iterate and Stop Each Process ---
    for process_name in target_process_names:
        try:
            # --- Determine the effective wait for *this specific process* ---
            is_worker = is_worker_process(process_name)
            effective_wait_for_this_process: bool
            skip_stop: bool = False


            if is_worker:
                if wait_workers is True:
                    effective_wait_for_this_process = True # Explicitly wait for worker
                elif wait_workers is False:
                    effective_wait_for_this_process = False # Explicitly DO NOT wait for worker
                    skip_stop = True
                else: # wait_workers is None (default)
                    effective_wait_for_this_process = wait # Use the global wait flag for worker
            else: # Not a worker process
                effective_wait_for_this_process = wait # Always use the global wait flag for non-workers

            if skip_stop:
                stop_results["already_stopped"].append(process_name)
            else:
                # Get current process state before attempting stop
                process_info = process_info_map.get(process_name)
                current_state = process_info.get('state') if process_info else None
                
                # Check if already stopped
                if current_state in STOPPED_STATES:
                    stop_results["already_stopped"].append(process_name)
                    continue
                
                # Call the single process handler with the calculated wait and process info
                success = _stop_single_process_with_logic(
                    supervisor_api,
                    service_name,
                    process_name,
                    wait=effective_wait_for_this_process, # Use calculated wait for API call
                    force_kill_timeout=force_kill_timeout,
                    wait_workers=wait_workers, # Pass original flag (True/False/None)
                    process_info=process_info_map.get(process_name),
                    verbose=verbose
                )
                
                if success:
                    stop_results["stopped"].append(process_name)
                else:
                    stop_results["failed"].append(process_name)
                    
        except Fault as e:
            # Catch faults raised by _stop_single_process_with_logic or its sub-helpers
            # _raise_exception_from_fault is already called inside the helper for specific faults
            # This catch is for unexpected faults during the helper execution itself
            display.error(f"Error during stop operation for process {process_name}: {e.faultString}")
            # Ensure _raise_exception_from_fault is called if not already handled
            # This might be redundant if helpers always call it, but acts as a safety net.
            try:
                _raise_exception_from_fault(e, service_name, action, process_name)
            except Exception: # Catch the exception raised by _raise_exception_from_fault
                pass # Exception is raised, loop continues or function exits
            stop_results["failed"].append(process_name)
        except Exception as e: # Catch non-Fault errors
            display.error(f"Unexpected error stopping process {display.highlight(process_name)}: {e}")
            stop_results["failed"].append(process_name)

    # Return detailed results instead of boolean
    return stop_results

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
            display.warning(f"No processes defined or found in {display.highlight(service_name)}. Nothing to start.")
            return {"started": [], "already_running": [], "failed": []}

        defined_process_names = {info['name'] for info in all_defined_processes_info}

        if process_names:
            # --- Case 1: Specific processes requested ---
            if verbose:
                display.print(f"Attempting to start specific process(es) in {display.highlight(service_name)}: {', '.join(process_names)}")
            missing_names = [name for name in process_names if name not in defined_process_names]
            if missing_names:
                display.warning(f"Specified process(es) not defined in supervisor config: {', '.join(missing_names)}")

            processes_to_start_explicitly = [name for name in process_names if name in defined_process_names]
            if not processes_to_start_explicitly:
                 display.error("None of the specified processes are defined. Nothing to start.")
                 return {"started": [], "already_running": [], "failed": process_names}  # Return failed list with requested names

        else:
            # --- Case 2: No specific processes requested -> Start ALL defined ---
            if verbose:
                display.print(f"Attempting to start all defined processes in {display.highlight(service_name)}...")
            processes_to_start_explicitly = list(defined_process_names)
            if not processes_to_start_explicitly:
                 display.print("  No processes defined to start.")
                 return start_results # Nothing to start is not an error

            # This check seems redundant now as processes_to_start_explicitly is assigned above
            # if not processes_to_start_explicitly:
            #      display.warning("  No suitable processes found to start (check worker state files?).")
            #      return {"started": [], "already_running": [], "failed": []}  # Nothing to start is not an error

        # --- Execute Start for the determined list ---
        if verbose:
            display.print(f"Final list of processes to start: {', '.join(processes_to_start_explicitly)}")

        # Create a map of name -> info for easy lookup
        process_info_map = {info['name']: info for info in all_defined_processes_info}

        for process_name_to_start in processes_to_start_explicitly:
            try:
                # Get the full info for the process we intend to start
                process_info = process_info_map.get(process_name_to_start)
                if not process_info:
                    # Should not happen if list was built correctly, but safety check
                    display.warning(f"Skipping {display.highlight(process_name_to_start)} - info not found.")
                    start_results["failed"].append(process_name_to_start)
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
                     display.error(f"Process {display.highlight(process_name_to_start)} entered FATAL state during start.")
                     # Add to 'failed' list
                     start_results["failed"].append(process_name_to_start)
                     _raise_exception_from_fault(start_fault, service_name, action, process_name_to_start)
                elif "BAD_NAME" in fault_string:
                     display.error(f"Process {display.highlight(process_name_to_start)} not found by supervisor (BAD_NAME).")
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
        return {"started": [], "already_running": [], "failed": [str(e)]}


# --- Helper: Restart Action ---

def _handle_restart(
    supervisor_api,
    service_name: str,
    process_names: Optional[List[str]], # Still unused, always restarts all
    wait: bool,
    force_kill_timeout: Optional[int] = None,
    # Accept Optional[bool] to match caller
    wait_workers: Optional[bool] = None
) -> bool:
    """Handle the 'restart' action by performing a standard stop-then-start sequence."""
    action = "restart"
    display.print(f"Initiating restart for {display.highlight(service_name)}...")

    # --- Standard Restart Strategy (Only strategy now) ---
    display.print("  Using Standard Restart strategy.")
    display.print(f"  Stopping all processes in {display.highlight(service_name)}...")

    # Pass None for process_names to stop all
    stop_results = _handle_stop(
        supervisor_api, service_name, None, wait, force_kill_timeout,
        wait_workers=wait_workers,
        called_from_restart=True
    )

    # Check if any processes failed to stop
    if stop_results.get("failed"):
        display.error(f"Failed to stop some processes during standard restart of {display.highlight(service_name)}. Aborting start.")
        raise SupervisorOperationFailedError("Failed to stop processes during standard restart", service_name=service_name)

    display.print(f"  Starting all defined processes in {display.highlight(service_name)}...")

    # Call _handle_start with None to start all defined processes
    _handle_start(supervisor_api, service_name, None, wait, verbose=False)

    # _handle_start now raises SupervisorOperationFailedError on failure
    # No need to check start_results["failed"] here, exception handling covers it

    display.success(f"Standard restart completed successfully for {display.highlight(service_name)}.")
    return True


# --- Helper: Info Action ---
def _handle_signal(supervisor_api, service_name: str, process_names: List[str], signal_name: str) -> bool:
    """Handle the 'signal' action."""
    action = "signal"
    results = {}
    signal_enum = getattr(signal, f"SIG{signal_name.upper()}", None)

    if signal_enum is None:
        display.error(f"Invalid signal name '{signal_name}' for service {display.highlight(service_name)}.")
        return False

    display.print(f"Sending signal {signal_name} ({int(signal_enum)}) to {len(process_names)} process(es) in {display.highlight(service_name)}...")

    if not process_names:
        display.print("  No specific processes provided to signal.")
        return True # Nothing to do

    # --- Get All Process Info Once ---
    try:
        all_info = supervisor_api.getAllProcessInfo()
        if not all_info:
            display.warning(f"No processes found running in {display.highlight(service_name)}. Cannot send signal.")
            return True # Nothing to signal if no processes exist
        # Create a map of simple name -> full info dict
        process_info_map = {info['name']: info for info in all_info}
    except Fault as e:
        display.error(f"Error getting process list for {display.highlight(service_name)} before signaling: {e.faultString}")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (signal)")
        return False # Indicate failure if we can't get the process list
    except Exception as e: # Catch other unexpected errors
        display.error(f"Unexpected error getting process list for {display.highlight(service_name)} before signaling: {e}")
        return False
    # --- End Get All Process Info ---

    for requested_name in process_names:
        # --- Look up info and construct API name ---
        process_info = process_info_map.get(requested_name)

        if not process_info:
            display.warning(f"Process {display.highlight(requested_name)} not found or not running in {display.highlight(service_name)}. Skipping signal.")
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
                display.dimmed(f"Process {display.highlight(requested_name)} not found by supervisor (BAD_NAME). Skipping signal.")
                results[requested_name] = True # Treat as success (process gone)
            elif "NOT_RUNNING" in fault_string:
                display.dimmed(f"Process {display.highlight(requested_name)} not running (NOT_RUNNING). Skipping signal.")
                results[requested_name] = True # Treat as success (process stopped)
            else:
                display.error(f"Error signaling process {display.highlight(requested_name)}: {e.faultString}")
                # Pass requested_name to the fault handler
                _raise_exception_from_fault(e, service_name, action, requested_name)
                results[requested_name] = False
        except Exception as e:
            # Use requested_name in messages
            display.error(f"Unexpected error signaling process {display.highlight(requested_name)}: {e}")
            results[requested_name] = False

    return all(results.values())

def _handle_signal_workers(
    supervisor_api,
    service_name: str,
    signal_num: int = SIGNAL_NUM_WORKER_GRACEFUL_EXIT,
) -> List[str]:
    """Identify worker processes and send them a specific signal.

    Args:
        supervisor_api: The connected supervisor XML-RPC proxy.
        service_name: Name of the service being targeted.
        signal_num: The numeric signal to send.

    Returns:
        List[str]: The names of the processes that were successfully signaled.

    Raises:
        SupervisorProcessError: If fetching process info fails.
    """
    signaled_processes = []
    action = "signal_workers" # For error reporting context
    try:
        all_info = supervisor_api.getAllProcessInfo()
    except Fault as e:
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (signal_workers)")
        # _raise_exception_from_fault will raise, but return empty list as fallback
        return []

    for proc in all_info:
        proc_name = proc.get('name', '')
        # Check if the process name indicates it's a worker
        if is_worker_process(proc_name):
            if proc.get('state') not in STOPPED_STATES: # Only signal running/starting processes
                try:
                    # Construct the fully qualified name
                    group_name = proc.get('group')
                    if not group_name:
                        # This case should ideally not happen if process info is complete
                        display.warning(f"Worker process {display.highlight(proc_name)} in {display.highlight(service_name)} is missing group information. Skipping signal.")
                        continue

                    name_for_api = f"{group_name}:{proc_name}"
                    display.info(f"Signaling worker process {display.highlight(name_for_api)} in {display.highlight(service_name)} with signal {signal_num}...")
                    supervisor_api.signalProcess(name_for_api, signal_num) # Use name_for_api
                    signaled_processes.append(proc_name)
                except Fault as e:
                    # Log warning but continue signaling others
                    display.warning(f"Failed to send signal {signal_num} to process {display.highlight(proc_name)} in {display.highlight(service_name)}: {e.faultString}")
    return signaled_processes

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
