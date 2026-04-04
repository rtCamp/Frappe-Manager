import socket
from typing import Optional, List, Any
from xmlrpc.client import ProtocolError
from fmx.supervisor.exceptions import SupervisorConnectionError
from fmx.supervisor.connection import check_supervisord_connection
from fmx.supervisor.actions import (
    _handle_stop,
    _handle_start,
    _handle_restart,
    _handle_info,
    _handle_signal,
)


def _get_validated_supervisor_api(service_name: str):
    """
    Validates supervisor connection and returns the API proxy.

    Logic:
    1. Calls check_supervisord_connection to verify service is reachable
    2. If connection fails: raises SupervisorConnectionError immediately
    3. If connection succeeds: returns the supervisor API proxy object
    4. API proxy is used for all subsequent supervisor operations

    Returns:
        Supervisor API proxy object ready for command execution

    Raises:
        SupervisorConnectionError: If service is unreachable or connection fails
    """
    conn = check_supervisord_connection(service_name)
    return conn.supervisor


def execute_supervisor_command(
    service_name: str,
    action: str,
    process_names: Optional[List[str]] = None,
    wait: bool = True,
    force_kill_timeout: Optional[int] = None,
    wait_workers: bool = False,
    verbose: bool = False,
    signal_name: Optional[str] = None,
    signal_num: Optional[int] = None,
    progress_callback=None,
) -> Any:
    """
    Central dispatcher for all supervisor operations with unified error handling.

    Logic:
    1. Validates supervisor connection first (fails fast if service unreachable)
    2. Dispatches to appropriate action handler based on action parameter:
       - "stop": Delegates to _handle_stop with force kill and worker options
       - "start": Delegates to _handle_start with validation and state tracking
       - "restart": Delegates to _handle_restart with complete stop-then-start
       - "info"/"INFO": Delegates to _handle_info for raw process data
       - "signal": Delegates to _handle_signal for targeted process signaling
    3. Wraps all operations in connection error handling
    4. Converts network/socket errors to SupervisorConnectionError for consistency
    5. Allows specific SupervisorErrors from handlers to propagate unchanged

    Returns:
        Action-specific results:
        - stop/start: Dict with process lists or boolean success
        - restart/signal: Boolean success status
        - info: List of process information dictionaries

    Raises:
        SupervisorConnectionError: For connection/network issues
        SupervisorError: For supervisor-specific operation failures
        ValueError: For invalid action or missing required parameters
    """
    supervisor_api = _get_validated_supervisor_api(service_name)

    try:
        if action == "stop":
            return _handle_stop(
                supervisor_api,
                service_name,
                process_names,
                wait,
                force_kill_timeout,
                wait_workers,
                verbose=verbose,
            )
        elif action == "start":
            return _handle_start(supervisor_api, service_name, process_names, wait, verbose=verbose)
        elif action == "restart":
            return _handle_restart(
                supervisor_api,
                service_name,
                process_names,
                wait,
                force_kill_timeout,
                wait_workers,
                progress_callback=progress_callback,
            )
        elif action == "info":
            return _handle_info(supervisor_api, service_name)
        elif action == "signal":
            if not signal_name:
                raise ValueError("Signal name must be provided for the 'signal' action.")
            if process_names is None:
                raise ValueError("Process names must be provided for the 'signal' action.")
            return _handle_signal(supervisor_api, service_name, process_names, signal_name)
        elif action == "INFO":
            return _handle_info(supervisor_api, service_name)
        else:
            raise ValueError(f"Unknown supervisor action requested: {action}")

    except (ProtocolError, ConnectionRefusedError, OSError, IOError, socket.error, socket.timeout) as e:
        raise SupervisorConnectionError(
            f"Connection error during '{action}': {e}", service_name=service_name, original_exception=e
        )
