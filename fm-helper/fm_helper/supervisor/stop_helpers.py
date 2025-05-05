import time
from typing import Optional, List, Any, Dict
from xmlrpc.client import Fault

from rich import print

from .constants import STOPPED_STATES, is_worker_process
from .fault_handler import _raise_exception_from_fault

def _wait_for_process_stop(supervisor_api, process_name: str, timeout: int) -> bool:
    """Wait for a single process to reach a stopped state."""
    print(f"  Waiting up to {timeout}s for graceful stop of [b green]{process_name}[/b green]...")
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        try:
            info = supervisor_api.getProcessInfo(process_name)
            if info['state'] in STOPPED_STATES:
                print(f"  Process [b green]{process_name}[/b green] stopped gracefully.")
                return True
        except Fault as e:
            # If process disappears during wait (e.g., BAD_NAME), consider it stopped.
            if "BAD_NAME" in e.faultString:
                 print(f"  Process [b green]{process_name}[/b green] disappeared, assuming stopped.")
                 return True
            # Re-raise other faults
            raise
        time.sleep(0.5)
    print(f"  [yellow]Timeout reached.[/yellow] Process [b green]{process_name}[/b green] did not stop gracefully.")
    return False

def _kill_process(supervisor_api, service_name: str, process_name: str) -> bool:
    """Send SIGKILL to a process and verify it stopped."""
    print(f"  Sending SIGKILL to process [b green]{process_name}[/b green]...")
    try:
        # Signal process KILL
        supervisor_api.signalProcess(process_name, 'KILL')
        # Short pause to allow OS to process the signal
        time.sleep(1)
        # Verify state after kill
        info = supervisor_api.getProcessInfo(process_name)
        if info['state'] in STOPPED_STATES:
            print(f"  Process [b green]{process_name}[/b green] killed successfully.")
            return True
        else:
            print(f"  [red]Error:[/red] Failed to kill process [b green]{process_name}[/b green]. Final state: {info['statename']}")
            return False
    except Fault as kill_fault:
        # Handle cases where the process was already dead before KILL
        if "ALREADY_DEAD" in kill_fault.faultString or "NOT_RUNNING" in kill_fault.faultString:
            print(f"  Process [b green]{process_name}[/b green] was already stopped before SIGKILL.")
            return True
        # Handle cases where the process doesn't exist anymore
        elif "BAD_NAME" in kill_fault.faultString:
            print(f"  Process [b green]{process_name}[/b green] not found, assuming stopped/killed.")
            return True
        else:
            # Re-raise unexpected faults during signal/getInfo
            print(f"  [red]Error sending SIGKILL or getting info for {process_name}:[/red] {kill_fault.faultString}")
            _raise_exception_from_fault(kill_fault, service_name, "signal/getInfo", process_name)
            return False # Should not be reached if _raise_exception_from_fault raises

def _wait_for_all_processes_stop(supervisor_api, service_name: str, timeout: int) -> bool:
    """Wait for all processes managed by supervisor to reach a stopped state."""
    print(f"  Waiting up to {timeout}s for graceful stop of all processes in [b magenta]{service_name}[/b magenta]...")
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        try:
            all_info = supervisor_api.getAllProcessInfo()
            # Handle case where supervisor might return empty list temporarily or if no processes exist
            if not all_info:
                 print("[yellow]Warning:[/yellow] No process info found while waiting for stop. Assuming all stopped.")
                 return True
            if all(info['state'] in STOPPED_STATES for info in all_info):
                print(f"  All processes in [b magenta]{service_name}[/b magenta] stopped gracefully.")
                return True
        except Fault as e:
             # If supervisor is shutting down during wait, consider it done.
             if "SHUTDOWN_STATE" in e.faultString:
                  print(f"  Supervisor in [b magenta]{service_name}[/b magenta] is shutting down, assuming processes stopped.")
                  return True
             # Re-raise other faults
             _raise_exception_from_fault(e, service_name, "getAllProcessInfo (wait)")
             return False # Should not be reached
        time.sleep(0.5)
    print(f"  [yellow]Timeout reached.[/yellow] Not all processes in [b magenta]{service_name}[/b magenta] stopped gracefully.")
    return False

def _kill_remaining_processes(supervisor_api, service_name: str) -> bool:
    """Find and kill processes that are not in a stopped state."""
    print(f"  Checking for running processes in [b magenta]{service_name}[/b magenta] to kill...")
    results = {}
    try:
        all_info = supervisor_api.getAllProcessInfo()
        processes_to_kill = [info for info in all_info if info['state'] not in STOPPED_STATES]

        if not processes_to_kill:
            print("  No running processes found to kill.")
            return True

        for info in processes_to_kill:
            process_name = info['name']
            print(f"  Attempting to kill process [b green]{process_name}[/b green] (State: {info['statename']})...")
            # Use the existing _kill_process helper
            results[process_name] = _kill_process(supervisor_api, service_name, process_name)

        return all(results.values())

    except Fault as e:
        # Handle errors during getAllProcessInfo itself
        print(f"  [red]Error getting process info to identify processes to kill:[/red] {e.faultString}")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (kill remaining)")
        return False # Indicate failure

def _wait_for_worker_processes_stop(supervisor_api, service_name: str, timeout: int) -> bool:
    """Wait specifically for worker processes to reach a stopped state."""
    worker_process_names = []
    try:
        all_info = supervisor_api.getAllProcessInfo()
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

def _stop_single_process_with_logic(
    supervisor_api,
    service_name: str,
    process_name: str,
    wait: bool,
    force_kill_timeout: Optional[int],
    wait_workers: Optional[bool],
    process_info: Optional[Dict[str, Any]] = None
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
        print(f"Attempting to stop process [b green]{original_process_name}[/b green] in [b magenta]{service_name}[/b magenta] (API wait: {wait})...")
        # Call stopProcess with the constructed name
        supervisor_api.stopProcess(name_to_stop, wait)

        # --- Force Kill Logic (runs independently of the 'wait' parameter for stopProcess) ---
        if force_kill_timeout is not None and force_kill_timeout > 0:
            # Worker process special handling (only if --wait-workers)
            worker_wait_timed_out = False
            if wait_workers and is_worker_process(original_process_name):
                print(f"  --wait-workers: Checking graceful stop for worker [b green]{original_process_name}[/b green] (timeout: {force_kill_timeout}s)...")
                worker_stopped_gracefully = _wait_for_process_stop(supervisor_api, original_process_name, force_kill_timeout)
                if not worker_stopped_gracefully:
                    worker_wait_timed_out = True
                    print(f"  [yellow]Warning:[/yellow] Worker process {original_process_name} did not stop gracefully within {force_kill_timeout}s.")
                else:
                    print(f"  Worker process [b green]{original_process_name}[/b green] stopped gracefully.")

            # General wait check (if worker wait didn't already time out)
            stopped_gracefully = False
            if not worker_wait_timed_out:
                print(f"  Verifying final stop state for [b green]{original_process_name}[/b green] (timeout: {force_kill_timeout}s)...")
                stopped_gracefully = _wait_for_process_stop(supervisor_api, original_process_name, force_kill_timeout)

            # Kill if the general wait failed or worker wait timed out
            if not stopped_gracefully:
                return _kill_process(supervisor_api, service_name, original_process_name)
            else:
                return True # Stopped gracefully within the force_kill_timeout

        # --- Non-Force Kill Reporting ---
        # If force_kill_timeout was NOT used, report based on the 'wait' flag passed to stopProcess
        else:
            if wait:
                # If stopProcess(wait=True) succeeded without Fault, assume it stopped.
                print(f"Stopped process [b green]{process_name}[/b green] in [b magenta]{service_name}[/b magenta] (waited).")
                return True
            else:
                # If stopProcess(wait=False) was called.
                print(f"Stop signal sent to process [b green]{process_name}[/b green] in [b magenta]{service_name}[/b magenta] (no wait).")
                return True # Assume success as signal was sent

    except Fault as e:
        fault_string = getattr(e, 'faultString', '') # Get fault string safely
        # Handle common "already stopped" or "doesn't exist" faults gracefully
        if "NOT_RUNNING" in fault_string:
            print(f"Process [b green]{process_name}[/b green] was already stopped.")
            return True
        elif "BAD_NAME" in fault_string:
            # Treat BAD_NAME during stop as if the process is already gone/stopped.
            # This usually happens due to a race condition where the process stops
            # between getting the list and issuing the stop command.
            group_name = process_info.get('group', 'N/A') if process_info else 'N/A'
            print(f"Process [b green]{process_name}[/b green] (Group: {group_name}) already stopped or gone before stop signal could be sent.")
            return True
        # Re-raise other faults for the main handler
        else:
            _raise_exception_from_fault(e, service_name, action, process_name)
            # This return False should ideally not be reached if _raise_exception_from_fault always raises
            return False
