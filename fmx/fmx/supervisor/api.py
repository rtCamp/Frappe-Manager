import logging
from typing import List, Optional, Dict, Any
from pathlib import Path
from rich.tree import Tree
from ..display import display

logger = logging.getLogger(__name__)

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
    logger.info(f"Discovering services from {FM_SUPERVISOR_SOCKETS_DIR}")
    if not FM_SUPERVISOR_SOCKETS_DIR.is_dir():
        logger.warning(f"Supervisor sockets directory not found: {FM_SUPERVISOR_SOCKETS_DIR}")
        return []

    services = sorted([
        file.stem
        for file in FM_SUPERVISOR_SOCKETS_DIR.glob("*.sock")
        if file.is_socket()
    ])
    logger.info(f"Found {len(services)} services: {services}")
    return services

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
    logger.info(f"Stop service called: service={service_name}, processes={process_name_list}, wait={wait}, force_kill_timeout={force_kill_timeout}, wait_workers={wait_workers}")
    try:
        result = execute_supervisor_command(
            service_name, "stop",
            process_names=process_name_list,
            wait=wait,
            force_kill_timeout=force_kill_timeout,
            wait_workers=wait_workers,
            verbose=verbose
        ) or False
        logger.info(f"Stop service completed: service={service_name}, success={bool(result)}")
        return result
    except SupervisorError as e:
        logger.error(f"Stop service failed: service={service_name}, error={str(e)}")
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
    logger.info(f"Start service called: service={service_name}, processes={process_name_list}, wait={wait}")
    try:
        result = execute_supervisor_command(
            service_name, "start",
            process_names=process_name_list,
            wait=wait,
            verbose=verbose
        )
        started_count = len(result.get("started", [])) if result else 0
        already_running_count = len(result.get("already_running", [])) if result else 0
        failed_count = len(result.get("failed", [])) if result else 0
        logger.info(f"Start service completed: service={service_name}, started={started_count}, already_running={already_running_count}, failed={failed_count}")
        return result
    except SupervisorError as e:
        logger.error(f"Start service failed: service={service_name}, error={str(e)}")
        raise e

def restart_service(
    service_name: str,
    wait: bool = True,
    wait_workers: Optional[bool] = None,
    force_kill_timeout: Optional[int] = None
) -> Dict[str, List[str]]:
    """
    Performs complete service restart using stop-then-start strategy.
    
    Logic:
    1. Stops ALL processes in the service (ignores any specific process selection)
    2. Waits for complete shutdown with optional force kill timeout
    3. If stop phase fails: aborts restart to prevent inconsistent state
    4. Starts ALL defined processes (fresh start from supervisor configuration)
    5. Returns detailed results combining both stop and start phases
    
    Returns:
        Dict with keys: 'stopped', 'already_stopped', 'started', 'already_running', 'failed'
    """
    logger.info(f"Restart service called: service={service_name}, wait={wait}, wait_workers={wait_workers}, force_kill_timeout={force_kill_timeout}")
    try:
        result = execute_supervisor_command(
            service_name, "restart",
            wait=wait,
            wait_workers=wait_workers,
            force_kill_timeout=force_kill_timeout
        )
        stopped_count = len(result.get("stopped", [])) if result else 0
        started_count = len(result.get("started", [])) if result else 0
        failed_count = len(result.get("failed", [])) if result else 0
        logger.info(f"Restart service completed: service={service_name}, stopped={stopped_count}, started={started_count}, failed={failed_count}")
        return result
    except SupervisorError as e:
        logger.error(f"Restart service failed: service={service_name}, error={str(e)}")
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
    logger.info(f"Signal service called: service={service_name}, signal={signal_name}, processes={process_name_list}")
    if not signal_name or not signal_name.isalnum():
        logger.error(f"Invalid signal name format: {signal_name}")
        display.error(f"Invalid signal name format: {signal_name}")
        return False
    if not process_name_list:
        logger.warning(f"No processes specified for signal '{signal_name}' in service '{service_name}'")
        display.warning(f"No process names specified for signal '{signal_name}' in service '{service_name}'.")
        return True

    try:
        result = execute_supervisor_command(
            service_name,
            "signal",
            process_names=process_name_list,
            signal_name=signal_name,
            # Other parameters like wait, force_kill etc. are not relevant for signal
        ) or False
        logger.info(f"Signal service completed: service={service_name}, signal={signal_name}, success={bool(result)}")
        return result
    except SupervisorError as e:
        logger.error(f"Signal service failed: service={service_name}, signal={signal_name}, error={str(e)}")
        raise e
    except ValueError as e:
        logger.error(f"Signal service validation failed: service={service_name}, signal={signal_name}, error={str(e)}")
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
    logger.info(f"Signal workers called: service={service_name}, signal_num={signal_num}")
    try:
        result = execute_supervisor_command(service_name, "signal_workers", signal_num=signal_num)
        worker_count = len(result) if result else 0
        logger.info(f"Signal workers completed: service={service_name}, workers_signaled={worker_count}, workers={result}")
        return result
    except Exception as e:
        logger.error(f"Signal workers failed: service={service_name}, error={str(e)}")
        raise e

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
    logger.info(f"Get service info called: service={service_name}, verbose={verbose}")
    try:
        if not check_supervisord_connection(service_name):
            logger.warning(f"Service {service_name} supervisor connection failed, returning empty info")
            return format_service_info(
                service_name,
                [],
                verbose=verbose
            )

        process_info = execute_supervisor_command(service_name, "INFO")
        process_count = len(process_info) if process_info else 0
        logger.info(f"Get service info completed: service={service_name}, process_count={process_count}")

        return format_service_info(service_name, process_info or [], verbose=verbose)

    except SupervisorConnectionError as e:
        logger.error(f"Get service info connection error: service={service_name}, error={str(e)}")
        return format_service_info(service_name, [], verbose=verbose)
    except SupervisorError as e:
        logger.error(f"Get service info failed: service={service_name}, error={str(e)}")
        return format_service_info(service_name, [], verbose=verbose)
