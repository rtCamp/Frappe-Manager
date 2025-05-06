class SupervisorError(Exception):
    """Base exception for fm-helper supervisor interactions."""
    def __init__(self, message, service_name=None, process_name=None, original_exception=None):
        self.service_name = service_name
        self.process_name = process_name
        self.original_exception = original_exception
        detail = f"Service: {service_name}" if service_name else ""
        if process_name:
            detail += f", Process: {process_name}"
        full_message = f"{message} ({detail})" if detail else message
        super().__init__(full_message)

class SupervisorProcessError(SupervisorError):
    """Exception related to specific process operations within a service."""
    def __init__(self, message: str, service_name: str, process_name: str | None = None, original_exception: Exception | None = None):
        self.process_name = process_name
        process_msg = f" (process: {process_name})" if process_name else ""
        # Call the base class __init__ correctly
        super().__init__(message=f"{message}{process_msg}", service_name=service_name, process_name=process_name, original_exception=original_exception)

class SupervisorConnectionError(SupervisorError):
    """Raised when connection to supervisord fails."""
    pass

class ProcessNotFoundError(SupervisorError):
    """Raised when a process name is not found by supervisord."""
    pass

class ProcessNotRunningError(SupervisorError):
    """Raised when trying to act on a process that isn't running."""
    pass

class ProcessAlreadyStartedError(SupervisorError):
    """Raised when trying to start an already started process."""
    pass

class SupervisorOperationFailedError(SupervisorError):
    """Raised for general operational failures reported by supervisord."""
    pass
