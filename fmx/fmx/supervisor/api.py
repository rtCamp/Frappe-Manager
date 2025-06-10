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
    """Get a list of service names based on available socket files."""
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
    """Stop specific processes or all processes in a service.
    
    If force_kill_timeout is provided, attempts graceful stop, waits for the
    timeout, and then sends SIGKILL if the process is still running.
    If wait_workers is True, adds an explicit check for worker processes stopping.
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
    state: Optional[str] = None,
    verbose: bool = False
) -> Optional[Dict[str, List[str]]]:
    """Start specific processes or all processes in a service, optionally targeting a state."""
    try:
        return execute_supervisor_command(
            service_name, "start",
            process_names=process_name_list,
            wait=wait,
            state=state,
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
    """Restart a service (all its processes) using standard stop-then-start.

    If force_kill_timeout is provided, it's used during the stop phase.
    The wait_workers flag influences stop behavior if specific worker waits are needed.
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
    """Send a signal to specific processes or all processes in a service."""
    # Basic validation for signal name (more can be added)
    if not signal_name or not signal_name.isalnum():
        display.error(f"Invalid signal name format: {signal_name}")
        return False
    if not process_name_list:
         display.warning(f"No process names specified for signal '{signal_name}' in service '{service_name}'.")
         return True # Assuming success if no processes targeted

    try:
        return execute_supervisor_command(
            service_name,
            "signal",
            process_names=process_name_list,
            signal_name=signal_name,
            # Other parameters like wait, force_kill etc. are not relevant for signal
        ) or False # Ensure boolean return
    except SupervisorError as e:
        raise e
    except ValueError as e: # Catch potential ValueError from execute_supervisor_command
         raise e

def signal_service_workers(
    service_name: str,
    signal_num: int = SIGNAL_NUM_WORKER_GRACEFUL_EXIT,
) -> List[str]:
    """Signal worker processes within a service for graceful shutdown.

    Args:
        service_name: The name of the target service.
        signal_num: The signal number to send. Defaults to graceful exit signal.

    Returns:
        List[str]: A list of process names that were signaled.

    Raises:
        SupervisorError: If the signaling operation fails.
    """
    # Use a distinct action name 'signal_workers' to trigger the specific handler
    return execute_supervisor_command(service_name, "signal_workers", signal_num=signal_num)

def get_service_info(service_name: str, verbose: bool = False) -> Tree:
    """Get information about a service and its processes.
    
    Args:
        service_name: Name of the service to get info for
        verbose: If True, returns detailed Tree output. If False, returns simple status line.
    """
    try:
        # Check connection first
        if not check_supervisord_connection(service_name):
            # Return formatted output even on connection error
            return format_service_info(
                service_name,
                [],
                verbose=verbose
            )

        # Use execute_supervisor_command to get the raw list
        # Use the placeholder "INFO" action
        process_info = execute_supervisor_command(service_name, "INFO")

        # Format the raw list using the existing formatter
        return format_service_info(service_name, process_info or [], verbose=verbose)

    # Add SupervisorConnectionError handling here
    except SupervisorConnectionError as e:
         # Handle connection errors gracefully by returning formatted output
         return format_service_info(service_name, [], verbose=verbose)
    except SupervisorError as e:
        # Handle other supervisor errors
        return format_service_info(service_name, [], verbose=verbose) # Return formatted empty info
