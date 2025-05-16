import socket
import socket
import signal
from typing import Optional, List, Any, Dict
from xmlrpc.client import ProtocolError

from .exceptions import SupervisorConnectionError
from .connection import check_supervisord_connection
from .actions import _handle_stop, _handle_start, _handle_restart, _handle_info, _handle_signal, _handle_signal_workers
from .constants import SIGNAL_NUM_WORKER_GRACEFUL_EXIT

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
    force_kill_timeout: Optional[int] = None,
    wait_workers: Optional[bool] = None,
    state: Optional[str] = None,
    verbose: bool = False,
    signal_name: Optional[str] = None,
    signal_num: Optional[int] = None,
) -> Any: # Return type depends on action
    """Execute supervisor commands, raising exceptions on failure.

    Dispatches to specific handlers based on the action.
    """
    # Get validated API proxy, raises SupervisorConnectionError on failure
    supervisor_api = _get_validated_supervisor_api(service_name)

    try:
        if action == "stop":
            # Pass force_kill_timeout to _handle_stop
            return _handle_stop(supervisor_api, service_name, process_names, wait, force_kill_timeout, wait_workers)
        elif action == "start":
            return _handle_start(supervisor_api, service_name, process_names, wait, state=state, verbose=verbose)
        elif action == "restart":
            # Pass force_kill_timeout and wait_workers to handle_restart
            return _handle_restart(
                supervisor_api,
                service_name,
                process_names,
                wait,
                force_kill_timeout,
                wait_workers
            )
        elif action == "info":
            # Returns raw list now
            return _handle_info(supervisor_api, service_name)
        elif action == "signal":
            if not signal_name:
                 raise ValueError("Signal name must be provided for the 'signal' action.")
            if process_names is None:
                 raise ValueError("Process names must be provided for the 'signal' action.")
            return _handle_signal(supervisor_api, service_name, process_names, signal_name)
        elif action == "signal_workers":
            # Use the provided signal_num or the default graceful exit signal
            effective_signal_num = signal_num if signal_num is not None else SIGNAL_NUM_WORKER_GRACEFUL_EXIT
            return _handle_signal_workers(supervisor_api, service_name, effective_signal_num)
        elif action == "INFO":
             # This is just a placeholder to reuse the executor logic for getting info
             # It relies on _handle_info returning the raw list
             return _handle_info(supervisor_api, service_name)
        else:
            raise ValueError(f"Unknown supervisor action requested: {action}")

    # Catch connection errors that might occur *during* an operation (after initial check)
    except (ProtocolError, ConnectionRefusedError, OSError, IOError, socket.error, socket.timeout) as e:
        # Re-raise as SupervisorConnectionError for consistent handling by the caller
        raise SupervisorConnectionError(f"Connection error during '{action}': {e}", service_name=service_name, original_exception=e)
    # Let specific SupervisorErrors (raised by helpers) and other unexpected errors propagate
