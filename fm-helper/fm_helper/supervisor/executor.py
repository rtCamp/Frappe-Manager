import socket
from typing import Optional, List, Any
from xmlrpc.client import ProtocolError

from .exceptions import SupervisorConnectionError
from .connection import check_supervisord_connection
from .actions import _handle_stop, _handle_start, _handle_restart, _handle_info

# --- Helper: Get Validated API ---
def _get_validated_supervisor_api(service_name: str):
    """Check connection and return the supervisor api proxy."""
    # check_supervisord_connection raises SupervisorConnectionError on failure
    conn = check_supervisord_connection(service_name)
    return conn.supervisor

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
            # Pass force_kill_timeout to _handle_stop
            return _handle_stop(supervisor_api, service_name, process_names, wait, force_kill_timeout)
        elif action == "start":
            return _handle_start(supervisor_api, service_name, process_names, wait)
        elif action == "restart":
            # Note: Restart currently doesn't support force_kill_timeout directly in its sequence
            # It relies on the _handle_stop call within it, which *doesn't* get the timeout here.
            # If force_kill is needed for restart, _handle_restart needs modification.
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
