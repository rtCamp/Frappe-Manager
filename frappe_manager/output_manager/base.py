"""
Base interface for output handling.

This abstract base class defines the contract that all output handlers must implement,
allowing business logic to be independent of the presentation layer.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any


class OutputHandler(ABC):
    """
    Abstract base class for handling output in business logic.

    This interface provides a unified API for different output mechanisms
    (CLI, API, testing, etc.) without coupling business logic to any specific
    presentation layer.
    """

    def __init__(self, verbose: bool = False):
        """
        Initialize output handler with verbosity settings.

        Args:
            verbose: Show info and debug level messages
        """
        self.verbose = verbose

    @abstractmethod
    def start(self, text: str) -> None:
        """
        Start a new operation with a status message.

        Args:
            text: The initial status message to display
        """

    @abstractmethod
    def change_head(self, text: str, style: str | None = None) -> None:
        """
        Update the current operation status message.

        Args:
            text: The new status message
            style: Optional style hint (implementation-specific)
        """

    @abstractmethod
    def update_head(self, text: str) -> None:
        """
        Update the head text (similar to change_head but with different semantics).

        Args:
            text: The new head text
        """

    @abstractmethod
    def stop(self) -> None:
        """
        Stop the current operation status display.
        """

    @abstractmethod
    def print(self, text: str, emoji_code: str = ":zap:", prefix: str | None = None, **kwargs) -> None:
        """
        Print a message.

        Args:
            text: The message to print
            emoji_code: Optional emoji code (implementation-specific)
            prefix: Optional prefix for the message
            **kwargs: Additional implementation-specific arguments
        """

    @abstractmethod
    def debug(self, text: str, emoji_code: str = ":bug:", **kwargs) -> None:
        """
        Display debug message (only shown if verbose=True).

        Args:
            text: Debug message
            emoji_code: Optional emoji code
            **kwargs: Additional implementation-specific arguments
        """

    @abstractmethod
    def info(self, text: str, emoji_code: str = ":information:", **kwargs) -> None:
        """
        Display info message (only shown if verbose=True).

        Args:
            text: Info message
            emoji_code: Optional emoji code
            **kwargs: Additional implementation-specific arguments
        """

    @abstractmethod
    def display_error(self, text: str, emoji_code: str = ":no_entry:") -> None:
        """
        Display error message without raising exception.

        Args:
            text: Error message
            emoji_code: Optional emoji code
        """

    @abstractmethod
    def error(self, text: str, exception: Exception, emoji_code: str = ":no_entry:") -> None:
        """
        Display an error message and raise the exception.

        This method always raises the provided exception after displaying the error message.
        Use display_error() if you want to display an error without raising an exception.

        Args:
            text: The error message
            exception: Exception to raise after displaying (required)
            emoji_code: Optional emoji code (implementation-specific)
        
        Raises:
            Exception: Always raises the provided exception
        """

    @abstractmethod
    def warning(self, text: str, emoji_code: str = ":warning:") -> None:
        """
        Display a warning message.

        Args:
            text: The warning message
            emoji_code: Optional emoji code (implementation-specific)
        """

    @abstractmethod
    def live_lines(
        self,
        data: Iterator[tuple[str, bytes]],
        stdout: bool = True,
        stderr: bool = True,
        lines: int = 4,
        padding: tuple[int, int, int, int] = (0, 0, 0, 2),
        stop_string: str | None = None,
        log_prefix: str = "=>",
    ) -> None:
        """
        Display live streaming output from a process.

        Args:
            data: Iterator yielding (source, line) tuples where source is "stdout" or "stderr"
            stdout: Whether to display stdout lines
            stderr: Whether to display stderr lines
            lines: Maximum number of lines to display
            padding: Padding around displayed lines (top, right, bottom, left)
            stop_string: String that stops display when found
            log_prefix: Prefix for each line
        """

    @abstractmethod
    def update_live(self, renderable: Any = None, padding: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        """
        Update the live display with new content.

        Args:
            renderable: Content to display (implementation-specific)
            padding: Padding around content (top, right, bottom, left)
        """

    @abstractmethod
    def prompt_ask(self, **kwargs) -> str:
        """
        Prompt the user for input.

        Args:
            **kwargs: Implementation-specific prompt arguments

        Returns:
            The user's input as a string
        """
