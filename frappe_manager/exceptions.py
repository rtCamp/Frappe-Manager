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



class OperationAborted(FrappeManagerException):
    """Raised when an operation is aborted by user or system."""



class ServiceNotAvailable(FrappeManagerException):
    """Raised when a required service is not available."""



class BenchOperationError(FrappeManagerException):
    """Raised when a bench operation fails."""



class SSLCertificateError(FrappeManagerException):
    """Raised when SSL certificate operations fail."""



class DockerOperationError(FrappeManagerException):
    """Raised when Docker operations fail."""



class MigrationError(FrappeManagerException):
    """Raised when migrations fail."""



class ConfigurationError(FrappeManagerException):
    """Raised when configuration is invalid or missing."""



class DependencyError(FrappeManagerException):
    """Raised when external dependencies are missing."""



class NonInteractiveError(FrappeManagerException):
    """Raised when interactive input required but CLI is non-interactive."""

    def __init__(self, message: str, suggestions: list[str] | None = None):
        """
        Initialize with error message and optional suggestions.

        Args:
            message: Human-readable error description
            suggestions: List of suggested solutions for the user
        """
        if suggestions:
            solutions_text = "\n".join(f"  • {s}" for s in suggestions)
            full_message = f"{message}\n\nSolutions:\n{solutions_text}"
        else:
            full_message = (
                f"{message}\n\n"
                "Solutions:\n"
                "  • Provide required arguments explicitly\n"
                "  • Run without --non-interactive for prompts\n"
                "  • Use --help for available options"
            )
        super().__init__(full_message)
