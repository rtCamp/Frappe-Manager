from typing import List, Optional, Dict, Any
from pathlib import Path
from rich.tree import Tree
from ..display import display

from .executor import execute_supervisor_command, check_supervisord_connection
from .constants import SIGNAL_NUM_WORKER_GRACEFUL_EXIT
from .connection import FM_SUPERVISOR_SOCKETS_DIR
from .status_formatter import format_service_info
from .exceptions import SupervisorError, SupervisorConnectionError


def get_service_names() -> List[str]:
    """
    Discovers available services by scanning for supervisor socket files.
    
    Logic:
    1. Checks if the supervisor sockets directory exists
    2. Scans for .sock files in the directory
    3. Extracts service names from socket filenames (removes .sock extension)
    4. Returns sorted list of service names
    
    Returns:
        List of service names found, empty if directory doesn't exist
    """
    if not FM_SUPERVISOR_SOCKETS_DIR.is_dir():
        return []

    return sorted([
        file.stem
        for file in FM_SUPERVISOR_SOCKETS_DIR.glob("*.sock")
        if file.is_socket()
    ])

def stop_service(
    service_name: str,
    process_name_list: Optional[List[str]] = None,
    wait: bool = True,
    force_kill_timeout: Optional[int] = None,
    wait_workers: Optional[bool] = None,
    verbose: bool = False
) -> bool:
    """
    Stops processes in a service with optional force kill and worker handling.
    
    Logic:
    1. Delegates to execute_supervisor_command with "stop" action
    2. If force_kill_timeout provided: stops gracefully first, waits, then force kills
    3. If wait_workers=True: waits specifically for worker processes to stop
    4. If process_name_list provided: stops only specified processes
    5. If process_name_list=None: stops all processes in the service
    
    Returns:
        True if all targeted processes stopped successfully, False otherwise
    """
    try:
        return execute_supervisor_command(
            service_name, "stop",
            process_names=process_name_list,
            wait=wait,
            force_kill_timeout=force_kill_timeout,
            wait_workers=wait_workers,
            verbose=verbose
        ) or False
    except SupervisorError as e:
        raise e

def start_service(
    service_name: str,
    process_name_list: Optional[List[str]] = None,
    wait: bool = True,
    verbose: bool = False
) -> Optional[Dict[str, List[str]]]:
    """
    Starts processes in a service with validation and state reporting.
    
    Logic:
    1. Delegates to execute_supervisor_command with "start" action
    2. If process_name_list provided: starts only specified processes
    3. If process_name_list=None: starts all defined processes in service
    4. Validates processes exist in supervisor configuration before starting
    5. Handles ALREADY_STARTED, FATAL, and BAD_NAME errors gracefully
    
    Returns:
        Dict with keys: 'started', 'already_running', 'failed' containing process lists
    """
    try:
        return execute_supervisor_command(
            service_name, "start",
            process_names=process_name_list,
            wait=wait,
            verbose=verbose
        )
    except SupervisorError as e:
        raise e

def restart_service(
    service_name: str,
    wait: bool = True,
    wait_workers: Optional[bool] = None,
    force_kill_timeout: Optional[int] = None
) -> bool:
    """
    Performs complete service restart using stop-then-start strategy.
    
    Logic:
    1. Stops ALL processes in the service (ignores any specific process selection)
    2. Waits for complete shutdown with optional force kill timeout
    3. If stop phase fails: aborts restart to prevent inconsistent state
    4. Starts ALL defined processes (fresh start from supervisor configuration)
    5. Returns overall success/failure status for the entire operation
    
    Returns:
        True if restart completed successfully, False if any phase failed
    """
    try:
        return execute_supervisor_command(
            service_name, "restart",
            wait=wait,
            wait_workers=wait_workers,
            force_kill_timeout=force_kill_timeout
        ) or False
    except SupervisorError as e:
        raise e

def signal_service(
    service_name: str,
    signal_name: str,
    process_name_list: Optional[List[str]] = None
) -> bool:
    """
    Sends Unix signals to specific processes in a service.
    
    Logic:
    1. Validates signal name format (basic alphanumeric check)
    2. If no processes specified: returns success (no-op)
    3. For each target process: sends signal via supervisor API
    4. Handles NOT_RUNNING and BAD_NAME cases gracefully (logs but continues)
    5. Returns True only if all signals sent successfully
    
    Returns:
        True if all signals sent successfully, False if any failed
    """
    if not signal_name or not signal_name.isalnum():
        display.error(f"Invalid signal name format: {signal_name}")
        return False
    if not process_name_list:
         display.warning(f"No process names specified for signal '{signal_name}' in service '{service_name}'.")
         return True

    try:
        return execute_supervisor_command(
            service_name,
            "signal",
            process_names=process_name_list,
            signal_name=signal_name,
            # Other parameters like wait, force_kill etc. are not relevant for signal
        ) or False
    except SupervisorError as e:
        raise e
    except ValueError as e:
         raise e

def signal_service_workers(
    service_name: str,
    signal_num: int = SIGNAL_NUM_WORKER_GRACEFUL_EXIT,
) -> List[str]:
    """
    Automatically identifies and signals all worker processes for graceful shutdown.
    
    Logic:
    1. Gets all running processes from supervisor
    2. Filters for worker processes using naming patterns (worker-, -worker, etc.)
    3. For each identified worker: sends specified signal (default: graceful exit)
    4. Constructs proper group:process API names for supervisor calls
    5. Returns list of worker process names that were successfully signaled
    
    Returns:
        List of process names that were signaled (empty if no workers found)
    """
    return execute_supervisor_command(service_name, "signal_workers", signal_num=signal_num)

def get_service_info(service_name: str, verbose: bool = False) -> Tree:
    """
    Retrieves and formats process information for a service as a Rich Tree.
    
    Logic:
    1. Checks supervisor connection first (handles disconnected services gracefully)
    2. If connected: retrieves all process info via execute_supervisor_command
    3. If disconnected: proceeds with empty process list (shows service as offline)
    4. Formats raw process data into Rich Tree structure using status_formatter
    5. Tree includes process states, PIDs, uptime, and other details
    
    Returns:
        Rich Tree object ready for display, even if service is offline/empty
    """
    try:
        if not check_supervisord_connection(service_name):
            return format_service_info(
                service_name,
                [],
                verbose=verbose
            )

        process_info = execute_supervisor_command(service_name, "INFO")

        return format_service_info(service_name, process_info or [], verbose=verbose)

    except SupervisorConnectionError as e:
         return format_service_info(service_name, [], verbose=verbose)
    except SupervisorError as e:
        return format_service_info(service_name, [], verbose=verbose)
