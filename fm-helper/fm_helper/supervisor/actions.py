import time
from typing import Optional, List, Any, Dict
from xmlrpc.client import Fault

from rich import print

from .constants import STOPPED_STATES
from .fault_handler import _raise_exception_from_fault

# --- Helper: Stop Action ---
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

def _stop_single_process_with_logic(
    supervisor_api,
    service_name: str,
    process_name: str,
    wait: bool,
    force_kill_timeout: Optional[int]
) -> bool:
    """Stop a single process, handling wait and force kill."""
    action = "stop"
    try:
        print(f"Attempting to stop process [b green]{process_name}[/b green] in [b magenta]{service_name}[/b magenta]...")
        # Determine if supervisor should wait internally based on force_kill presence
        effective_wait = wait and not (force_kill_timeout and force_kill_timeout > 0)
        supervisor_api.stopProcess(process_name, effective_wait)

        # If force_kill is requested, wait externally and potentially kill
        if force_kill_timeout is not None and force_kill_timeout > 0:
            stopped_gracefully = _wait_for_process_stop(supervisor_api, process_name, force_kill_timeout)
            if not stopped_gracefully:
                return _kill_process(supervisor_api, service_name, process_name)
            else:
                return True # Stopped gracefully within timeout
        # If no force kill, but wait was requested (and handled by supervisor)
        elif effective_wait:
            print(f"Stopped process [b green]{process_name}[/b green] in [b magenta]{service_name}[/b magenta] (waited).")
            return True
        # If no force kill and no wait requested
        else:
            print(f"Stop signal sent to process [b green]{process_name}[/b green] in [b magenta]{service_name}[/b magenta] (no wait).")
            return True # Signal sent, success assumed unless wait is required

    except Fault as e:
        # Handle common "already stopped" fault gracefully
        if "NOT_RUNNING" in e.faultString:
            print(f"Process [b green]{process_name}[/b green] was already stopped.")
            return True
        # Re-raise other faults for the main handler
        else:
            _raise_exception_from_fault(e, service_name, action, process_name)
            return False # Should not be reached

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

def _stop_all_processes_with_logic(
    supervisor_api,
    service_name: str,
    wait: bool,
    force_kill_timeout: Optional[int]
) -> bool:
    """Stop all processes, handling wait and force kill."""
    action = "stopAll"
    print(f"Attempting to stop all processes in [b magenta]{service_name}[/b magenta]...")
    try:
        # If no force kill, use the simpler supervisor call
        if force_kill_timeout is None or force_kill_timeout <= 0:
            supervisor_api.stopAllProcesses(wait)
            print(f"Stopped all processes in [b magenta]{service_name}[/b magenta] {'(waited)' if wait else '(no wait)'}.")
            return True

        # --- Force kill logic for 'stop all' ---
        print(f"  Initiating stop for all processes (no wait)...")
        supervisor_api.stopAllProcesses(wait=False)

        # Wait externally
        all_stopped_gracefully = _wait_for_all_processes_stop(supervisor_api, service_name, force_kill_timeout)

        if all_stopped_gracefully:
            return True
        else:
            # If timeout reached, attempt to kill remaining
            return _kill_remaining_processes(supervisor_api, service_name)

    except Fault as e:
        # Re-raise faults for the main handler
        _raise_exception_from_fault(e, service_name, action)
        return False # Should not be reached

def _handle_stop(
    supervisor_api,
    service_name: str,
    process_names: Optional[List[str]],
    wait: bool,
    force_kill_timeout: Optional[int] = None
) -> bool:
    """Handle the 'stop' action using helper functions."""
    action = "stop" # Used for potential top-level exception reporting

    if process_names:
        # --- Case 1: Stop specific processes ---
        results = {}
        for process in process_names:
            try:
                # Delegate the complex logic to the helper
                results[process] = _stop_single_process_with_logic(
                    supervisor_api, service_name, process, wait, force_kill_timeout
                )
            except Fault as e:
                 # Catch faults raised by _stop_single_process_with_logic or its sub-helpers
                 # _raise_exception_from_fault is already called inside the helper for specific faults
                 # This catch is for unexpected faults during the helper execution itself
                 print(f"[red]Error during stop operation for process {process}: {e.faultString}[/red]")
                 # Ensure _raise_exception_from_fault is called if not already handled
                 # This might be redundant if helpers always call it, but acts as a safety net.
                 try:
                     _raise_exception_from_fault(e, service_name, action, process)
                 except Exception: # Catch the exception raised by _raise_exception_from_fault
                     pass # Exception is raised, loop continues or function exits
                 results[process] = False # Mark as failed if exception occurred
            except Exception as e: # Catch non-Fault errors
                 print(f"[red]Unexpected error stopping process {process}: {e}[/red]")
                 results[process] = False
        # Return True only if all individual stop operations succeeded
        return all(results.values())

    else:
        # --- Case 2: Stop all processes ---
        try:
            # Delegate the complex logic to the helper
            return _stop_all_processes_with_logic(
                supervisor_api, service_name, wait, force_kill_timeout
            )
        except Fault as e:
            # Catch faults raised by _stop_all_processes_with_logic or its sub-helpers
            print(f"[red]Error during stop all operation for service {service_name}: {e.faultString}[/red]")
            try:
                _raise_exception_from_fault(e, service_name, action)
            except Exception:
                pass
            return False # Indicate failure
        except Exception as e: # Catch non-Fault errors
            print(f"[red]Unexpected error stopping all processes in service {service_name}: {e}[/red]")
            return False


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
def _handle_restart(supervisor_api, service_name: str, process_names: Optional[List[str]], wait: bool) -> bool:
    """Handle the 'restart' action by performing a stop/start sequence."""
    print(f"[b blue]{service_name}[/b blue] - Stopping all processes...")
    # Use internal call to stop, respecting 'wait'. Can raise exceptions.
    _handle_stop(supervisor_api, service_name, process_names, wait) # Use helper
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
