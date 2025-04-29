import time
from typing import Optional, List, Any, Dict
from xmlrpc.client import Fault, ProtocolError

from rich import print
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
def _handle_stop(supervisor_api, service_name: str, process_names: Optional[List[str]], wait: bool) -> bool:
    """Handle the 'stop' action."""
    action = "stop"
    try:
        if process_names:
            results = {}
            for process in process_names:
                # stopProcess returns True on success, raises Fault on failure
                supervisor_api.stopProcess(process, wait)
                print(f"Stopped process [b green]{process}[/b green] in [b magenta]{service_name}[/b magenta]")
                results[process] = True
            return all(results.values()) # Return True if all individual stops succeeded
        else:
            # stopAllProcesses raises Fault on failure for any process
            supervisor_api.stopAllProcesses(wait)
            print(f"Stopped all processes in [b magenta]{service_name}[/b magenta]")
            return True # If no Fault was raised, assume success
    except Fault as e:
        _raise_exception_from_fault(e, service_name, action, process_names[0] if process_names else None)


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
    wait: bool = True
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
