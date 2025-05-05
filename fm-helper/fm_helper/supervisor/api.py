from typing import List, Optional, Dict, Any
from pathlib import Path
from rich.tree import Tree

from .executor import execute_supervisor_command, check_supervisord_connection
from .connection import FM_SUPERVISOR_SOCKETS_DIR
from .status_formatter import format_service_info
from .exceptions import SupervisorError, SupervisorConnectionError
from .constants import DEFAULT_SUFFIXES

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
    wait_workers: bool = False
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
            wait_workers=wait_workers
        ) or False
    except SupervisorError as e:
        print(f"[red]Error stopping {service_name}:[/red] {str(e)}")
        return None

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
        print(f"[red]Error starting {service_name}:[/red] {str(e)}")
        return False

def restart_service(
    service_name: str,
    wait: bool = True,
    wait_workers: bool = False,
    force_kill_timeout: Optional[int] = None,
    # Add missing parameters needed by execute_supervisor_command -> _handle_restart
    suffixes: str = DEFAULT_SUFFIXES,
    rolling_timeout: int = 60
) -> bool:
    """Restart a service (all its processes).

    If wait_workers is True, uses standard stop-then-start.
    If wait_workers is False (default), uses hybrid rolling restart for workers.
    If force_kill_timeout is provided, it's used during the stop phase(s).
    suffixes and rolling_timeout are used for hybrid restart.
    """
    try:
        return execute_supervisor_command(
            service_name, "restart",
            wait=wait,
            wait_workers=wait_workers,
            force_kill_timeout=force_kill_timeout,
            # Pass the new parameters down
            suffixes=suffixes,
            rolling_timeout=rolling_timeout
        ) or False
    except SupervisorError as e:
        print(f"[red]Error restarting {service_name}:[/red] {str(e)}")
        return False

def signal_service(
    service_name: str,
    signal_name: str,
    process_name_list: Optional[List[str]] = None
) -> bool:
    """Send a signal to specific processes or all processes in a service."""
    # Basic validation for signal name (more can be added)
    if not signal_name or not signal_name.isalnum():
        print(f"[red]Invalid signal name format: {signal_name}[/red]")
        return False
    if not process_name_list:
         print(f"[yellow]Warning:[/yellow] No process names specified for signal '{signal_name}' in service '{service_name}'.")
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
        print(f"[red]Error signaling {signal_name} in {service_name}:[/red] {str(e)}")
        return False
    except ValueError as e: # Catch potential ValueError from execute_supervisor_command
         print(f"[red]Error during signal operation for {service_name}:[/red] {str(e)}")
         return False

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
         print(f"[yellow]Connection Error getting info for {service_name}:[/yellow] {str(e)}")
         return format_service_info(service_name, [], verbose=verbose)
    except SupervisorError as e:
        # Handle other supervisor errors
        print(f"[red]Error getting info for {service_name}:[/red] {str(e)}")
        return format_service_info(service_name, [], verbose=verbose) # Return formatted empty info
