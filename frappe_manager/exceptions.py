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

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
        suggestions: list[str] | None = None,
    ):
        """
        Initialize exception with message and optional details.

        Args:
            message: Human-readable error message
            details: Optional dictionary with additional context (for logging/API)
            suggestions: What the operator can do next, rendered by main.py's handler
        """
        self.message = message
        self.details = details or {}
        # On the BASE, not on one subclass: `NonInteractiveError` grew its own `Solutions:` block
        # and was for a long time the only error in fm that said what to do next, so the harder
        # cases -- a bench name that is wrong rather than absent -- had no help at all.
        self.suggestions = suggestions or []
        super().__init__(self.message)

    # Click exits 2 on a usage error and fm's handler exits 1, so the same mistake answered
    # differently depending on which half caught it (`fm create` 2, `fm info` 1). A subclass
    # sets 2 to mean "the command line was wrong"; see docs/commands/index.md.
    exit_code = 1

    def to_dict(self) -> dict[str, Any]:
        """
        Convert exception to dictionary for API responses.

        Returns:
            Dictionary with error_type, message, and details
        """
        return {"error_type": self.__class__.__name__, "message": self.message, "details": self.details}


class ConfigurationError(FrappeManagerException):
    """Raised when configuration is invalid or missing."""


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


class MissingArgumentError(NonInteractiveError):
    """A required argument was not given and could not be asked for.

    Exits 2, the code the parser already uses for the same mistake. Deliberately narrower than
    its parent: `NonInteractiveError` also covers refusals like "pass --yes", where fm understood
    the command line and declined to act, which is a 1.
    """

    exit_code = 2


class InvalidBenchNameError(FrappeManagerException):
    """A bench or site name that is not a legal hostname.

    Exit 2: the name came off the command line, so this is a wrong command line, the same class of
    mistake as an unknown flag.
    """

    exit_code = 2


class VersionUnusableByApps(FrappeManagerException):
    """An explicitly requested Python or Node version that the bench's apps reject.

    Exit 2: the version came off the command line. Raised as soon as the app's own requirement is
    known, because the alternative is discovering it from a dependency resolver minutes and
    gigabytes later.
    """

    exit_code = 2
