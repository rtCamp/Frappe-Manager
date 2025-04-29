from typing import List, Optional
from pathlib import Path

from .executor import execute_supervisor_command, check_supervisord_connection
from .connection import FM_SUPERVISOR_SOCKETS_DIR
from .status_formatter import format_service_info
from .exceptions import SupervisorError

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
    force_kill_timeout: Optional[int] = None
) -> bool:
    """Stop specific processes or all processes in a service.
    
    If force_kill_timeout is provided, attempts graceful stop, waits for the
    timeout, and then sends SIGKILL if the process is still running.
    """
    try:
        return execute_supervisor_command(
            service_name, "stop",
            process_names=process_name_list,
            wait=wait,
            force_kill_timeout=force_kill_timeout
        ) or False
    except SupervisorError as e:
        print(f"[red]Error stopping {service_name}:[/red] {str(e)}")
        return False

def start_service(
    service_name: str,
    process_name_list: Optional[List[str]] = None,
    wait: bool = True
) -> bool:
    """Start specific processes or all processes in a service."""
    try:
        return execute_supervisor_command(
            service_name, "start",
            process_names=process_name_list,
            wait=wait
        ) or False
    except SupervisorError as e:
        print(f"[red]Error starting {service_name}:[/red] {str(e)}")
        return False

def restart_service(
    service_name: str,
    wait: bool = True
) -> bool:
    """Restart a service (all its processes)."""
    try:
        return execute_supervisor_command(
            service_name, "restart",
            wait=wait
        ) or False
    except SupervisorError as e:
        print(f"[red]Error restarting {service_name}:[/red] {str(e)}")
        return False

def get_service_info(service_name: str):
    """Get detailed information about a service and its processes."""
    try:
        if not check_supervisord_connection(service_name):
            return format_service_info(
                service_name,
                []
            )

        process_info = execute_supervisor_command(service_name, "info")
        return format_service_info(service_name, process_info or [])

    except SupervisorError as e:
        print(f"[red]Error getting info for {service_name}:[/red] {str(e)}")
        return format_service_info(service_name, [])
