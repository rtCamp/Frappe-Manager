from typing import List, Optional
from pathlib import Path

from .executor import execute_supervisor_command, is_supervisord_running
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
    wait: bool = True
) -> bool:
    """Stop specific processes or all processes in a service."""
    try:
        return execute_supervisor_command(
            service_name, "stop",
            process_names=process_name_list,
            wait=wait
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
    force: bool = False,
    wait: bool = True
) -> bool:
    """Restart a service (all its processes)."""
    try:
        return execute_supervisor_command(
            service_name, "restart",
            force=force,
            wait=wait
        ) or False
    except SupervisorError as e:
        print(f"[red]Error restarting {service_name}:[/red] {str(e)}")
        return False

def get_service_info(service_name: str):
    """Get detailed information about a service and its processes."""
    try:
        if not is_supervisord_running(service_name):
            return format_service_info(
                service_name,
                []
            )

        process_info = execute_supervisor_command(service_name, "info")
        return format_service_info(service_name, process_info or [])

    except SupervisorError as e:
        print(f"[red]Error getting info for {service_name}:[/red] {str(e)}")
        return format_service_info(service_name, [])
