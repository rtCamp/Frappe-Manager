import logging
import time
from typing import Optional, List, Any, Dict
from xmlrpc.client import Fault

from ..display import display

logger = logging.getLogger(__name__)

from .constants import STOPPED_STATES, is_worker_process
from .fault_handler import _raise_exception_from_fault

def _wait_for_process_stop(supervisor_api, process_name: str, timeout: int) -> bool:
    """Wait for a single process to reach a stopped state."""
    logger.info(f"Waiting up to {timeout}s for graceful stop of {process_name}")
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        try:
            info = supervisor_api.getProcessInfo(process_name)
            if info['state'] in STOPPED_STATES:
                logger.info(f"Process {process_name} stopped gracefully")
                return True
        except Fault as e:
            # If process disappears during wait (e.g., BAD_NAME), consider it stopped.
            if "BAD_NAME" in e.faultString:
                logger.info(f"Process {process_name} disappeared, assuming stopped")
                return True
            # Re-raise other faults
            raise
        time.sleep(0.5)
    logger.warning(f"Timeout reached. Process {process_name} did not stop gracefully")
    return False

def _kill_process(supervisor_api, service_name: str, process_name: str) -> bool:
    """Send SIGKILL to a process and verify it stopped."""
    logger.info(f"Sending SIGKILL to process {process_name}")
    try:
        # Signal process KILL
        supervisor_api.signalProcess(process_name, 'KILL')
        # Short pause to allow OS to process the signal
        time.sleep(1)
        # Verify state after kill
        info = supervisor_api.getProcessInfo(process_name)
        if info['state'] in STOPPED_STATES:
            logger.info(f"Process {process_name} killed successfully")
            return True
        else:
            logger.error(f"Failed to kill process {process_name}. Final state: {info['statename']}")
            return False
    except Fault as kill_fault:
        # Handle cases where the process was already dead before KILL
        if "ALREADY_DEAD" in kill_fault.faultString or "NOT_RUNNING" in kill_fault.faultString:
            logger.info(f"Process {process_name} was already stopped before SIGKILL")
            return True
        # Handle cases where the process doesn't exist anymore
        elif "BAD_NAME" in kill_fault.faultString:
            logger.info(f"Process {process_name} not found, assuming stopped/killed")
            return True
        else:
            # Re-raise unexpected faults during signal/getInfo
            logger.error(f"Error sending SIGKILL to {process_name}: {kill_fault.faultString}")
            _raise_exception_from_fault(kill_fault, service_name, "signal/getInfo", process_name)
            return False # Should not be reached if _raise_exception_from_fault raises



def _wait_for_worker_processes_stop(supervisor_api, service_name: str, timeout: int) -> bool:
    """Wait specifically for worker processes to reach a stopped state."""
    worker_process_names = []
    try:
        all_info = supervisor_api.getAllProcessInfo()
        # Identify worker process names
        worker_process_names = [info['name'] for info in all_info if is_worker_process(info['name'])]
    except Fault as e:
        display.error(f"Error getting process info to identify workers: {e.faultString}")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (worker wait identify)")
        return False  # Indicate failure to even identify workers

    if not worker_process_names:
        display.print("  No worker processes found to wait for.")
        return True  # No workers means the condition is met

    num_workers = len(worker_process_names)
    # Use display.highlight for worker names
    worker_names_str = ", ".join(display.highlight(name) for name in worker_process_names)
    display.print(f"  Waiting up to {timeout}s for {num_workers} worker process(es) ({worker_names_str}) to stop...")

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
                display.success(f"  All {num_workers} identified worker process(es) stopped gracefully.")
                return True

        except Fault as e:
            # If supervisor is shutting down during wait, consider it done.
            if "SHUTDOWN_STATE" in e.faultString:
                display.print(f"  Supervisor in {display.highlight(service_name)} is shutting down, assuming workers stopped.")
                return True
            # Handle other potential faults during getAllProcessInfo
            display.error(f"Error checking worker status: {e.faultString}")
            # Don't raise here, just log and assume not stopped for this check
            all_workers_stopped_this_check = False

        # Wait only if not all workers were stopped in this check
        if not all_workers_stopped_this_check:
            time.sleep(0.5)

    display.warning(f"Timeout reached. Not all identified worker processes stopped gracefully.")
    return False

def _stop_single_process_with_logic(
    supervisor_api,
    service_name: str,
    process_name: str,
    wait: bool,
    force_kill_timeout: Optional[int],
    wait_workers: Optional[bool],
    process_info: Optional[Dict[str, Any]] = None,
    verbose: bool = False
) -> bool:
    """Stop a single process, respecting the 'wait' flag for the API call and handling force kill separately."""
    action = "stop"

    # Determine the name to use for the stop command
    original_process_name = process_name
    group_name = process_info.get('group') if process_info else None
    name_to_stop = original_process_name

    # Construct 'group:name' format if needed
    if group_name and not original_process_name.startswith(f"{group_name}:"):
        name_to_stop = f"{group_name}:{original_process_name}"

    try:
        if verbose:
            display.print(f"Attempting to stop process {display.highlight(original_process_name)} in {display.highlight(service_name)} (API wait: {wait})...")

        supervisor_api.stopProcess(name_to_stop, wait)

        # --- Force Kill Logic (runs independently of the 'wait' parameter for stopProcess) ---
        if force_kill_timeout is not None and force_kill_timeout > 0:
            is_worker = is_worker_process(original_process_name)
            
            if is_worker and wait_workers is False:
                # --no-wait-workers means detachment occurred, use monitor process logic
                logger.info(f"Worker {original_process_name} detached, allowing monitor process {min(5, force_kill_timeout)}s")
                time.sleep(min(5, force_kill_timeout))
                return True
            elif is_worker:
                # Normal worker restart/stop (with or without --wait-workers)
                # Worker is still under supervisor control, send extra TERM via supervisor
                logger.info(f"Checking graceful stop for worker {original_process_name} (timeout: {force_kill_timeout}s)")
                stopped_gracefully = _wait_for_process_stop(supervisor_api, original_process_name, force_kill_timeout)
                
                if not stopped_gracefully:
                    logger.info(f"Worker {original_process_name} didn't stop gracefully, sending additional TERM")
                    try:
                        supervisor_api.signalProcess(name_to_stop, 'TERM')
                        # Give a bit more time after the additional TERM
                        time.sleep(3)
                        logger.info(f"Additional TERM sent to worker {original_process_name}")
                        return True
                    except Fault as e:
                        logger.warning(f"Failed to send additional TERM to worker {original_process_name}: {e.faultString}")
                        return False
                else:
                    logger.info(f"Worker {original_process_name} stopped gracefully")
                    return True
            else:
                # Non-workers get normal force kill logic (TERM → wait → KILL)
                logger.info(f"Checking graceful stop for non-worker {original_process_name} (timeout: {force_kill_timeout}s)")
                stopped_gracefully = _wait_for_process_stop(supervisor_api, original_process_name, force_kill_timeout)
                
                if not stopped_gracefully:
                    logger.info(f"Non-worker {original_process_name} didn't stop gracefully, force killing")
                    return _kill_process(supervisor_api, service_name, original_process_name)
                else:
                    logger.info(f"Non-worker {original_process_name} stopped gracefully")
                    return True

        # --- Non-Force Kill Reporting ---
        # If force_kill_timeout was NOT used, report based on the 'wait' flag passed to stopProcess
        else:
            if wait:
                # If stopProcess(wait=True) succeeded without Fault, assume it stopped.
                if verbose:
                    display.success(f"Stopped process {display.highlight(process_name)} in {display.highlight(service_name)} (waited).")
                return True
            else:
                # If stopProcess(wait=False) was called.
                if verbose:
                    display.print(f"Stop signal sent to process {display.highlight(process_name)} in {display.highlight(service_name)} (no wait).")
                return True # Assume success as signal was sent

    except Fault as e:
        fault_string = getattr(e, 'faultString', '') # Get fault string safely
        # Handle common "already stopped" or "doesn't exist" faults gracefully
        if "NOT_RUNNING" in fault_string:
            display.print(f"Process {display.highlight(process_name)} was already stopped.")
            return True
        elif "BAD_NAME" in fault_string:
            # Treat BAD_NAME during stop as if the process is already gone/stopped.
            # This usually happens due to a race condition where the process stops
            # between getting the list and issuing the stop command.
            group_name = process_info.get('group', 'N/A') if process_info else 'N/A'
            display.print(f"Process {display.highlight(process_name)} (Group: {group_name}) already stopped or gone before stop signal could be sent.")
            return True
        # Re-raise other faults for the main handler
        else:
            _raise_exception_from_fault(e, service_name, action, process_name)
            # This return False should ideally not be reached if _raise_exception_from_fault always raises
            return False
