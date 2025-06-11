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
    """
    Stops processes in a service with intelligent worker handling.
    
    Logic:
    1. Gets all running processes from supervisor
    2. Determines target processes (specified ones or all)
    3. For each process, applies worker-specific stop logic:
       - Workers: respects wait_workers parameter for graceful shutdown
       - Non-workers: uses standard wait parameter
    4. Optionally applies force kill timeout for stubborn processes
    5. Returns categorized results (stopped, already_stopped, failed)
    """
    action = "stop"
    target_process_names: List[str] = []
    process_info_map: Dict[str, Dict[str, Any]] = {}
    
    stop_results = {"stopped": [], "already_stopped": [], "failed": []}

    try:
        all_info = supervisor_api.getAllProcessInfo()
        if not all_info:
            display.print(f"No processes found running in {display.highlight(service_name)}.")
            return stop_results
        process_info_map = {info['name']: info for info in all_info}
    except Fault as e:
        display.error(f"Error getting process list for {service_name}: {e.faultString}")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (stop)")
        return {"stopped": [], "already_stopped": [], "failed": ["<unexpected error>"]}
    if process_names:
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
        target_process_names = list(process_info_map.keys())
        if verbose:
            display.print(f"Preparing to stop all processes in {display.highlight(service_name)}...")

    if not called_from_restart and verbose:
        if wait_workers is True:
            display.dimmed("Stop calls for worker processes WILL wait for graceful shutdown.")
        elif wait_workers is False:
            display.dimmed("Stop calls for worker processes will not stop it and not wait for graceful shutdown.")
    for process_name in target_process_names:
        try:
            is_worker = is_worker_process(process_name)
            effective_wait_for_this_process: bool
            skip_stop: bool = False

            if is_worker:
                if wait_workers is True:
                    effective_wait_for_this_process = True
                elif wait_workers is False:
                    effective_wait_for_this_process = False
                    skip_stop = True
                else:
                    effective_wait_for_this_process = wait
            else:
                effective_wait_for_this_process = wait

            if skip_stop:
                stop_results["already_stopped"].append(process_name)
            else:
                process_info = process_info_map.get(process_name)
                current_state = process_info.get('state') if process_info else None
                
                if current_state in STOPPED_STATES:
                    stop_results["already_stopped"].append(process_name)
                    continue
                
                success = _stop_single_process_with_logic(
                    supervisor_api,
                    service_name,
                    process_name,
                    wait=effective_wait_for_this_process,
                    force_kill_timeout=force_kill_timeout,
                    wait_workers=wait_workers,
                    process_info=process_info_map.get(process_name),
                    verbose=verbose
                )
                
                if success:
                    stop_results["stopped"].append(process_name)
                else:
                    stop_results["failed"].append(process_name)
                    
        except Fault as e:
            display.error(f"Error during stop operation for process {process_name}: {e.faultString}")
            try:
                _raise_exception_from_fault(e, service_name, action, process_name)
            except Exception:
                pass
            stop_results["failed"].append(process_name)
        except Exception as e:
            display.error(f"Unexpected error stopping process {display.highlight(process_name)}: {e}")
            stop_results["failed"].append(process_name)

    return stop_results

def _handle_start(supervisor_api, service_name: str, process_names: Optional[List[str]], wait: bool, state: Optional[str] = None, verbose: bool = False) -> Dict[str, List[str]]:
    """
    Starts processes in a service with validation and conflict detection.
    
    Logic:
    1. Gets all defined processes from supervisor configuration
    2. Validates requested processes exist in the configuration
    3. For each target process:
       - Constructs proper API name (handles group prefixes)
       - Attempts to start the process
       - Handles ALREADY_STARTED, FATAL, and BAD_NAME errors gracefully
    4. Returns categorized results (started, already_running, failed)
    """
    action = "start"
    processes_to_start_explicitly: List[str] = []
    start_results = {"started": [], "already_running": [], "failed": []}

    try:
        all_defined_processes_info = supervisor_api.getAllProcessInfo()
        if not all_defined_processes_info:
            display.warning(f"No processes defined or found in {display.highlight(service_name)}. Nothing to start.")
            return {"started": [], "already_running": [], "failed": []}

        defined_process_names = {info['name'] for info in all_defined_processes_info}

        if process_names:
            if verbose:
                display.print(f"Attempting to start specific process(es) in {display.highlight(service_name)}: {', '.join(process_names)}")
            missing_names = [name for name in process_names if name not in defined_process_names]
            if missing_names:
                display.warning(f"Specified process(es) not defined in supervisor config: {', '.join(missing_names)}")

            processes_to_start_explicitly = [name for name in process_names if name in defined_process_names]
            if not processes_to_start_explicitly:
                 display.error("None of the specified processes are defined. Nothing to start.")
                 return {"started": [], "already_running": [], "failed": process_names}

        else:
            if verbose:
                display.print(f"Attempting to start all defined processes in {display.highlight(service_name)}...")
            processes_to_start_explicitly = list(defined_process_names)
            if not processes_to_start_explicitly:
                 display.print("  No processes defined to start.")
                 return start_results

        if verbose:
            display.print(f"Final list of processes to start: {', '.join(processes_to_start_explicitly)}")

        process_info_map = {info['name']: info for info in all_defined_processes_info}

        for process_name_to_start in processes_to_start_explicitly:
            try:
                process_info = process_info_map.get(process_name_to_start)
                if not process_info:
                    display.warning(f"Skipping {display.highlight(process_name_to_start)} - info not found.")
                    start_results["failed"].append(process_name_to_start)
                    continue

                group_name = process_info.get('group')
                name_for_api = process_name_to_start
                if group_name and not process_name_to_start.startswith(f"{group_name}:"):
                    name_for_api = f"{group_name}:{process_name_to_start}"

                supervisor_api.startProcess(name_for_api, wait)
                start_results["started"].append(process_name_to_start)
            except Fault as start_fault:
                fault_string = getattr(start_fault, 'faultString', '')
                if "ALREADY_STARTED" in fault_string:
                    start_results["already_running"].append(process_name_to_start)
                elif "FATAL" in fault_string:
                     display.error(f"Process {display.highlight(process_name_to_start)} entered FATAL state during start.")
                     start_results["failed"].append(process_name_to_start)
                     _raise_exception_from_fault(start_fault, service_name, action, process_name_to_start)
                elif "BAD_NAME" in fault_string:
                     display.error(f"Process {display.highlight(process_name_to_start)} not found by supervisor (BAD_NAME).")
                     start_results["failed"].append(process_name_to_start)
                     _raise_exception_from_fault(start_fault, service_name, action, process_name_to_start)
                else:
                    start_results["failed"].append(process_name_to_start)
                    _raise_exception_from_fault(start_fault, service_name, action, process_name_to_start)

        return start_results

    except Fault as e:
        _raise_exception_from_fault(e, service_name, action, process_names[0] if process_names else None)
        return {"started": [], "already_running": [], "failed": processes_to_start_explicitly or ["<error getting process list>"]}
    except Exception as e:
        return {"started": [], "already_running": [], "failed": [str(e)]}


def _handle_restart(
    supervisor_api,
    service_name: str,
    process_names: Optional[List[str]],
    wait: bool,
    force_kill_timeout: Optional[int] = None,
    wait_workers: Optional[bool] = None
) -> bool:
    """
    Performs a complete service restart using stop-then-start strategy.
    
    Logic:
    1. Stops ALL processes in the service (ignores process_names parameter)
    2. Waits for complete shutdown with optional force kill
    3. If stop fails, aborts the restart to prevent inconsistent state
    4. Starts ALL defined processes (fresh start from configuration)
    5. Returns success/failure status for the entire operation
    """
    action = "restart"
    display.print(f"Initiating restart for {display.highlight(service_name)}...")

    display.print("  Using Standard Restart strategy.")
    display.print(f"  Stopping all processes in {display.highlight(service_name)}...")

    stop_results = _handle_stop(
        supervisor_api, service_name, None, wait, force_kill_timeout,
        wait_workers=wait_workers,
        called_from_restart=True
    )

    if stop_results.get("failed"):
        display.error(f"Failed to stop some processes during standard restart of {display.highlight(service_name)}. Aborting start.")
        raise SupervisorOperationFailedError("Failed to stop processes during standard restart", service_name=service_name)

    display.print(f"  Starting all defined processes in {display.highlight(service_name)}...")

    _handle_start(supervisor_api, service_name, None, wait, verbose=False)

    display.success(f"Standard restart completed successfully for {display.highlight(service_name)}.")
    return True

def _handle_signal(supervisor_api, service_name: str, process_names: List[str], signal_name: str) -> bool:
    """
    Sends Unix signals to specific processes with validation and error handling.
    
    Logic:
    1. Validates the signal name exists in Python's signal module
    2. Gets current process list to verify targets exist
    3. For each target process:
       - Constructs proper API name (handles group prefixes)
       - Sends the signal via supervisor
       - Gracefully handles NOT_RUNNING and BAD_NAME cases
    4. Returns True if all signals sent successfully, False otherwise
    """
    action = "signal"
    results = {}
    signal_enum = getattr(signal, f"SIG{signal_name.upper()}", None)

    if signal_enum is None:
        display.error(f"Invalid signal name '{signal_name}' for service {display.highlight(service_name)}.")
        return False

    display.print(f"Sending signal {signal_name} ({int(signal_enum)}) to {len(process_names)} process(es) in {display.highlight(service_name)}...")

    if not process_names:
        display.print("  No specific processes provided to signal.")
        return True

    try:
        all_info = supervisor_api.getAllProcessInfo()
        if not all_info:
            display.warning(f"No processes found running in {display.highlight(service_name)}. Cannot send signal.")
            return True
        process_info_map = {info['name']: info for info in all_info}
    except Fault as e:
        display.error(f"Error getting process list for {display.highlight(service_name)} before signaling: {e.faultString}")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (signal)")
        return False
    except Exception as e:
        display.error(f"Unexpected error getting process list for {display.highlight(service_name)} before signaling: {e}")
        return False

    for requested_name in process_names:
        process_info = process_info_map.get(requested_name)

        if not process_info:
            display.warning(f"Process {display.highlight(requested_name)} not found or not running in {display.highlight(service_name)}. Skipping signal.")
            results[requested_name] = True
            continue

        group_name = process_info.get('group')
        name_for_api = requested_name

        if group_name and not requested_name.startswith(f"{group_name}:"):
            name_for_api = f"{group_name}:{requested_name}"

        try:
            supervisor_api.signalProcess(name_for_api, signal_name.upper())
            results[requested_name] = True
        except Fault as e:
            fault_string = getattr(e, 'faultString', '')
            if "BAD_NAME" in fault_string:
                display.dimmed(f"Process {display.highlight(requested_name)} not found by supervisor (BAD_NAME). Skipping signal.")
                results[requested_name] = True
            elif "NOT_RUNNING" in fault_string:
                display.dimmed(f"Process {display.highlight(requested_name)} not running (NOT_RUNNING). Skipping signal.")
                results[requested_name] = True
            else:
                display.error(f"Error signaling process {display.highlight(requested_name)}: {e.faultString}")
                _raise_exception_from_fault(e, service_name, action, requested_name)
                results[requested_name] = False
        except Exception as e:
            display.error(f"Unexpected error signaling process {display.highlight(requested_name)}: {e}")
            results[requested_name] = False

    return all(results.values())

def _handle_signal_workers(
    supervisor_api,
    service_name: str,
    signal_num: int = SIGNAL_NUM_WORKER_GRACEFUL_EXIT,
) -> List[str]:
    """
    Automatically identifies and signals all worker processes for graceful shutdown.
    
    Logic:
    1. Gets all running processes from supervisor
    2. Filters for worker processes using naming patterns (worker-, -worker, etc.)
    3. For each identified worker that's not already stopped:
       - Sends graceful exit signal (default: signal 34)
       - Constructs proper group:process API name
    4. Returns list of successfully signaled worker process names
    """
    signaled_processes = []
    action = "signal_workers"
    try:
        all_info = supervisor_api.getAllProcessInfo()
    except Fault as e:
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (signal_workers)")
        return []

    for proc in all_info:
        proc_name = proc.get('name', '')
        if is_worker_process(proc_name):
            if proc.get('state') not in STOPPED_STATES:
                try:
                    group_name = proc.get('group')
                    if not group_name:
                        display.warning(f"Worker process {display.highlight(proc_name)} in {display.highlight(service_name)} is missing group information. Skipping signal.")
                        continue

                    name_for_api = f"{group_name}:{proc_name}"
                    display.info(f"Signaling worker process {display.highlight(name_for_api)} in {display.highlight(service_name)} with signal {signal_num}...")
                    supervisor_api.signalProcess(name_for_api, signal_num)
                    signaled_processes.append(proc_name)
                except Fault as e:
                    display.warning(f"Failed to send signal {signal_num} to process {display.highlight(proc_name)} in {display.highlight(service_name)}: {e.faultString}")
    return signaled_processes

def _handle_info(supervisor_api, service_name: str) -> List[Dict[str, Any]]:
    """
    Retrieves raw process information from supervisor for status display.
    
    Logic:
    1. Calls supervisor's getAllProcessInfo() API
    2. Returns the raw process data as a list of dictionaries
    3. Each dict contains: name, state, pid, uptime, etc.
    4. Used by status/info commands for detailed process information
    """
    action = "info"
    try:
        info_list = supervisor_api.getAllProcessInfo()
        return info_list if isinstance(info_list, list) else []
    except Fault as e:
        _raise_exception_from_fault(e, service_name, action)
