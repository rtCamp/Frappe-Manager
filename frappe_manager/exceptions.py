"""
Frappe Manager exception hierarchy.

All custom exceptions inherit from FrappeManagerException to allow
catching all FM-specific errors in one place. This enables consistent
error handling across different interfaces (CLI, API, WebSocket, etc.).
"""

from typing import Any


class FrappeManagerException(Exception):
    """
    Base exception for all Frappe Manager errors.

    All custom exceptions should inherit from this to enable:
    - Consistent error handling across different interfaces
    - Easy differentiation from third-party exceptions
    - Structured error information for API responses
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        """
        Initialize exception with message and optional details.

        Args:
            message: Human-readable error message
            details: Optional dictionary with additional context (for logging/API)
        """
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert exception to dictionary for API responses.

        Returns:
            Dictionary with error_type, message, and details
        """
        return {"error_type": self.__class__.__name__, "message": self.message, "details": self.details}


class ValidationError(FrappeManagerException):
    """Raised when input validation fails."""

    pass


class OperationAborted(FrappeManagerException):
    """Raised when an operation is aborted by user or system."""

    pass


class ServiceNotAvailable(FrappeManagerException):
    """Raised when a required service is not available."""

    pass


class BenchOperationError(FrappeManagerException):
    """Raised when a bench operation fails."""

    pass


class SSLCertificateError(FrappeManagerException):
    """Raised when SSL certificate operations fail."""

    pass


class DockerOperationError(FrappeManagerException):
    """Raised when Docker operations fail."""

    pass


class MigrationError(FrappeManagerException):
    """Raised when migrations fail."""

    pass


class ConfigurationError(FrappeManagerException):
    """Raised when configuration is invalid or missing."""

    pass


class DependencyError(FrappeManagerException):
    """Raised when external dependencies are missing."""

    pass
