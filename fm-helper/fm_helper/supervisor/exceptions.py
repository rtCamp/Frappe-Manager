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
