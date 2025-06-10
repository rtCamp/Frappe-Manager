from typing import Optional
from xmlrpc.client import Fault

from .exceptions import (
    SupervisorConnectionError,
    ProcessNotFoundError,
    ProcessNotRunningError,
    ProcessAlreadyStartedError,
    SupervisorOperationFailedError
)

def _raise_exception_from_fault(e: Fault, service_name: str, action: str, process_name: Optional[str] = None):
    """Raise a specific SupervisorError based on the XML-RPC Fault."""
    fault_code = getattr(e, 'faultCode', None)
    fault_string = getattr(e, 'faultString', 'Unknown Fault')

    # Map fault strings/codes to specific exceptions
    if "BAD_NAME" in fault_string:
        raise ProcessNotFoundError(f"Process not found: {fault_string}", service_name, process_name, e)
    elif "NOT_RUNNING" in fault_string:
        raise ProcessNotRunningError(f"Process not running: {fault_string}", service_name, process_name, e)
    elif "ALREADY_STARTED" in fault_string:
        raise ProcessAlreadyStartedError(f"Process already started: {fault_string}", service_name, process_name, e)
    elif "BAD_ARGUMENTS" in fault_string:
        raise SupervisorOperationFailedError(f"Invalid arguments: {fault_string}", service_name, process_name, e)
    elif "NO_FILE" in fault_string:
        raise SupervisorConnectionError(f"Socket file error: {fault_string}", service_name, process_name, e) # Treat as connection issue
    elif "FAILED" in fault_string:
         raise SupervisorOperationFailedError(f"Action failed: {fault_string}", service_name, process_name, e)
    elif "SHUTDOWN_STATE" in fault_string:
        raise SupervisorConnectionError(f"Supervisor is shutting down", service_name, process_name, e) # Treat as connection issue
    # Add more specific mappings here if needed
    else:
        # Generic fallback
        raise SupervisorOperationFailedError(f"Supervisor Fault {fault_code or 'N/A'}: '{fault_string}'", service_name, process_name, e)
