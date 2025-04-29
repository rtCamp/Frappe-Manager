import time
from typing import Optional, List, Any, Dict
from xmlrpc.client import Fault, ProtocolError
from enum import IntEnum

from rich import print

# Define Supervisor Process States Constants
class ProcessStates(IntEnum):
    STOPPED = 0
    STARTING = 10
    RUNNING = 20
    BACKOFF = 30
    STOPPING = 40
    EXITED = 100
    FATAL = 200
    UNKNOWN = 1000

STOPPED_STATES = (ProcessStates.STOPPED, ProcessStates.EXITED, ProcessStates.FATAL)
from .exceptions import (
    SupervisorError,
    SupervisorConnectionError,
    ProcessNotFoundError,
    ProcessNotRunningError,
    ProcessAlreadyStartedError, # Added
    SupervisorOperationFailedError # Added
)
# Import check_supervisord_connection instead of get_xml_connection directly
from .connection import check_supervisord_connection
import socket # Added for specific connection errors

# REMOVED: is_supervisord_running - replaced by check_supervisord_connection

def _raise_exception_from_fault(e: Fault, service_name: str, action: str, process_name: Optional[str] = None):
    """Raise a specific SupervisorError based on the XML-RPC Fault."""
    fault_code = getattr(e, 'faultCode', None)
    fault_string = getattr(e, 'faultString', 'Unknown Fault')

    # Map fault strings/codes to specific exceptions
    if "BAD_NAME" in fault_string:
        raise ProcessNotFoundError(f"Process not found: {fault_string}", service_name, process_name, e)
    elif "NOT_RUNNING" in fault_string:
        raise ProcessNotRunningError(f"Process not running: {fault_string}", service_name, process_name, e)
    elif "ALREADY_STARTED" in fault_string:
        raise ProcessAlreadyStartedError(f"Process already started: {fault_string}", service_name, process_name, e)
    elif "BAD_ARGUMENTS" in fault_string:
        raise SupervisorOperationFailedError(f"Invalid arguments: {fault_string}", service_name, process_name, e)
    elif "NO_FILE" in fault_string:
        raise SupervisorConnectionError(f"Socket file error: {fault_string}", service_name, process_name, e) # Treat as connection issue
    elif "FAILED" in fault_string:
         raise SupervisorOperationFailedError(f"Action failed: {fault_string}", service_name, process_name, e)
    elif "SHUTDOWN_STATE" in fault_string:
        raise SupervisorConnectionError(f"Supervisor is shutting down", service_name, process_name, e) # Treat as connection issue
    # Add more specific mappings here if needed
    else:
        # Generic fallback
        raise SupervisorOperationFailedError(f"Supervisor Fault {fault_code or 'N/A'}: '{fault_string}'", service_name, process_name, e)


# --- Helper: Get Validated API ---
def _get_validated_supervisor_api(service_name: str):
    """Check connection and return the supervisor api proxy."""
    # check_supervisord_connection raises SupervisorConnectionError on failure
    conn = check_supervisord_connection(service_name)
    return conn.supervisor


# --- Helper: Stop Action ---
def _handle_stop(
    supervisor_api,
    service_name: str,
    process_names: Optional[List[str]],
    wait: bool,
    force_kill_timeout: Optional[int] = None
) -> bool:
    """Handle the 'stop' action, with optional force kill logic."""
    action = "stop"

    # --- Case 1: Stop specific processes ---
    if process_names:
        results = {}
        for process in process_names:
            process_stopped = False
            try:
                print(f"Attempting to stop process [b green]{process}[/b green] in [b magenta]{service_name}[/b magenta]...")
                # Use wait=False if force_kill_timeout is active, otherwise use provided wait
                effective_wait = wait and not force_kill_timeout
                supervisor_api.stopProcess(process, effective_wait)

                if force_kill_timeout is not None and force_kill_timeout > 0:
                    # Force kill logic for individual process
                    print(f"  Waiting up to {force_kill_timeout}s for graceful stop...")
                    start_time = time.monotonic()
                    while time.monotonic() - start_time < force_kill_timeout:
                        info = supervisor_api.getProcessInfo(process)
                        if info['state'] in STOPPED_STATES:
                            print(f"  Process [b green]{process}[/b green] stopped gracefully.")
                            process_stopped = True
                            break
                        time.sleep(0.5)

                    if not process_stopped:
                        print(f"  [yellow]Timeout reached.[/yellow] Process [b green]{process}[/b green] still running. Sending SIGKILL...")
                        try:
                            supervisor_api.signalProcess(process, 'KILL')
                            time.sleep(1)
                            info = supervisor_api.getProcessInfo(process)
                            if info['state'] in STOPPED_STATES:
                                print(f"  Process [b green]{process}[/b green] killed successfully.")
                                process_stopped = True
                            else:
                                print(f"  [red]Error:[/red] Failed to kill process [b green]{process}[/b green]. Final state: {info['statename']}")
                                process_stopped = False
                        except Fault as kill_fault:
                            if "ALREADY_DEAD" in kill_fault.faultString or "NOT_RUNNING" in kill_fault.faultString:
                                print(f"  Process [b green]{process}[/b green] was already stopped before SIGKILL.")
                                process_stopped = True
                            else:
                                print(f"  [red]Error sending SIGKILL to {process}:[/red] {kill_fault.faultString}")
                                _raise_exception_from_fault(kill_fault, service_name, "signal", process)
                                process_stopped = False

                elif effective_wait:
                    process_stopped = True
                    print(f"Stopped process [b green]{process}[/b green] in [b magenta]{service_name}[/b magenta] (waited).")
                else:
                    process_stopped = True
                    print(f"Stop signal sent to process [b green]{process}[/b green] in [b magenta]{service_name}[/b magenta] (no wait).")

                results[process] = process_stopped

            except Fault as e:
                if "NOT_RUNNING" in e.faultString:
                    print(f"Process [b green]{process}[/b green] was already stopped.")
                    results[process] = True
                else:
                    _raise_exception_from_fault(e, service_name, action, process)
                    results[process] = False
        return all(results.values())

    # --- Case 2: Stop all processes (process_names is None) ---
    else:
        print(f"Attempting to stop all processes in [b magenta]{service_name}[/b magenta]...")
        try:
            # If no force kill, just use stopAllProcesses with the specified wait
            if force_kill_timeout is None or force_kill_timeout <= 0:
                supervisor_api.stopAllProcesses(wait)
                print(f"Stopped all processes in [b magenta]{service_name}[/b magenta] {'(waited)' if wait else '(no wait)'}.")
                return True

            # --- Force kill logic for 'stop all' ---
            print(f"  Initiating stop for all processes (no wait)...")
            supervisor_api.stopAllProcesses(wait=False)

            print(f"  Waiting up to {force_kill_timeout}s for graceful stop of all processes...")
            start_time = time.monotonic()
            all_stopped_gracefully = False
            target_processes_info = []

            while time.monotonic() - start_time < force_kill_timeout:
                target_processes_info = supervisor_api.getAllProcessInfo()
                if not target_processes_info:
                    print("[yellow]Warning:[/yellow] No process info found after initiating stop.")
                    return True

                if all(info['state'] in STOPPED_STATES for info in target_processes_info):
                    print(f"  All processes stopped gracefully.")
                    all_stopped_gracefully = True
                    break
                time.sleep(0.5)

            if all_stopped_gracefully:
                return True

            # Timeout reached, identify running processes and kill them
            print(f"  [yellow]Timeout reached.[/yellow] Checking for running processes to kill...")
            results = {}
            processes_to_kill = [info for info in target_processes_info if info['state'] not in STOPPED_STATES]

            if not processes_to_kill:
                print("  No running processes found after timeout.")
                return True

            for info in processes_to_kill:
                process = info['name']
                process_killed = False
                print(f"  Sending SIGKILL to process [b green]{process}[/b green] (State: {info['statename']})...")
                try:
                    supervisor_api.signalProcess(process, 'KILL')
                    time.sleep(0.5)
                    final_info = supervisor_api.getProcessInfo(process)
                    if final_info['state'] in STOPPED_STATES:
                        print(f"    Process [b green]{process}[/b green] killed successfully.")
                        process_killed = True
                    else:
                        print(f"    [red]Error:[/red] Failed to kill process [b green]{process}[/b green]. Final state: {final_info['statename']}")
                        process_killed = False
                except Fault as kill_fault:
                    if "ALREADY_DEAD" in kill_fault.faultString or "NOT_RUNNING" in kill_fault.faultString:
                        print(f"    Process [b green]{process}[/b green] was already stopped before SIGKILL.")
                        process_killed = True
                    else:
                        print(f"    [red]Error sending SIGKILL to {process}:[/red] {kill_fault.faultString}")
                        process_killed = False
                results[process] = process_killed

            return all(results.values())

        except Fault as e:
            _raise_exception_from_fault(e, service_name, action)
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


# --- Main Executor Function ---
def execute_supervisor_command(
    service_name: str,
    action: str,
    process_names: Optional[List[str]] = None,
    wait: bool = True,
    force_kill_timeout: Optional[int] = None
) -> Any: # Return type depends on action
    """Execute supervisor commands, raising exceptions on failure.

    Dispatches to specific handlers based on the action.
    """
    # Get validated API proxy, raises SupervisorConnectionError on failure
    supervisor_api = _get_validated_supervisor_api(service_name)

    try:
        if action == "stop":
            return _handle_stop(supervisor_api, service_name, process_names, wait)
        elif action == "start":
            return _handle_start(supervisor_api, service_name, process_names, wait)
        elif action == "restart":
            return _handle_restart(supervisor_api, service_name, process_names, wait)
        elif action == "info":
            return _handle_info(supervisor_api, service_name)
        else:
            raise ValueError(f"Unknown supervisor action requested: {action}")

    # Catch connection errors that might occur *during* an operation (after initial check)
    except (ProtocolError, ConnectionRefusedError, OSError, IOError, socket.error, socket.timeout) as e:
        # Re-raise as SupervisorConnectionError for consistent handling by the caller
        raise SupervisorConnectionError(f"Connection error during '{action}': {e}", service_name=service_name, original_exception=e)
    # Let specific SupervisorErrors (raised by helpers) and other unexpected errors propagate
