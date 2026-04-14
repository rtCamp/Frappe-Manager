from typing import List, Tuple, Optional, Dict
from fmx.display import DisplayManager
from fmx.supervisor.connection import FM_SUPERVISOR_SOCKETS_DIR

MESSAGES: Dict[str, str] = {
    "NO_SERVICES_FOUND": "No supervisord services found to {action}.",
    "SOCKET_DIR_INFO": f"Looked for socket files in: {FM_SUPERVISOR_SOCKETS_DIR}",
    "ENSURE_RUNNING": "Ensure Frappe Manager services are running.",
    "INVALID_SERVICES": "Invalid service name(s): {services}",
    "AVAILABLE_SERVICES": "Available services: {services_list}",
}


def validate_services(
    display: DisplayManager,
    services_to_target: List[str],
    all_services: List[str],
    action_desc: str = "process",
) -> Tuple[bool, Optional[str]]:
    """
    Common service validation with standard error messages.

    Args:
        display: DisplayManager instance for output
        services_to_target: List of services to validate
        all_services: List of all available services
        action_desc: Description of the action being performed (e.g., "start", "stop")

    Returns:
        Tuple[bool, Optional[str]]:
            - bool: True if validation passed, False if failed
            - str: Target description for successful validation (e.g., "all services" or "service(s): xyz")
            - None if validation failed
    """
    if not all_services:
        display.error(MESSAGES["NO_SERVICES_FOUND"].format(action=action_desc), exit_code=1)
        display.print(MESSAGES["SOCKET_DIR_INFO"])
        display.print(MESSAGES["ENSURE_RUNNING"])
        return False, None

    invalid_services = [s for s in services_to_target if s not in all_services]
    if invalid_services:
        display.error(MESSAGES["INVALID_SERVICES"].format(services=', '.join(invalid_services)), exit_code=1)
        display.print(MESSAGES["AVAILABLE_SERVICES"].format(services_list=', '.join(all_services) or 'None'))
        return False, None

    target_desc = (
        "all services"
        if len(services_to_target) == len(all_services)
        else f"service(s): {display.highlight(', '.join(services_to_target))}"
    )

    return True, target_desc


def format_wait_desc(wait: bool) -> str:
    """Return a human-readable wait mode label for display messages.

    Args:
        wait: True if the command will wait for operations to complete.

    Returns:
        str: "(with wait)" or "(without wait)"
    """
    return "(with wait)" if wait else "(without wait)"


def resolve_service_targets(service_names_enum_list, all_services: List[str]) -> List[str]:
    """Resolve CLI service name arguments to a concrete list of service name strings.

    If no service names are specified (i.e. the list is falsy / None), all
    available services are targeted.

    Args:
        service_names_enum_list: A list of Enum members whose ``.value`` is the
            service name string, or any falsy value (``None``, ``[]``) to
            indicate "all services".
        all_services: The full list of available service name strings.

    Returns:
        List[str]: Service names to act on.
    """
    if not service_names_enum_list:
        return all_services
    return [s.value for s in service_names_enum_list]


def get_process_description(display: DisplayManager, process_names: Optional[List[str]] = None) -> str:
    """
    Generate a consistent description of targeted processes.

    Args:
        display: DisplayManager instance for styling
        process_names: Optional list of specific process names

    Returns:
        str: Description of targeted processes
    """
    if not process_names:
        return "all processes"
    return f"process(es): {display.highlight(', '.join(process_names))}"
