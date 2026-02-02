"""
Base interface for output handling.

This abstract base class defines the contract that all output handlers must implement,
allowing business logic to be independent of the presentation layer.
"""

import sys
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from typing import Any, Optional


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
        self._spinner_active = False
        self._current_text: Optional[str] = None

        # Interactive mode state (3-level priority system)
        self._interactive: Optional[bool] = None  # None = not initialized
        self._tty_available: bool = sys.stdin.isatty() and sys.stdout.isatty()

    @abstractmethod
    def start(self, text: str) -> None:
        """
        Start a new operation with a status message.

        Args:
            text: The initial status message to display
        """
        self._spinner_active = True
        self._current_text = text

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
        self._spinner_active = False

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
        data: Iterable[tuple[str, bytes]],
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
            data: Iterable yielding (source, line) tuples where source is "stdout" or "stderr"
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

    def set_interactive_mode(self, non_interactive_flag: bool) -> None:
        """
        Set interactive mode from global --non-interactive flag and TTY detection.

        This implements priority level 2 & 3 of the interactive mode system:
        - Priority 1 (command flags like --force) handled in prompt_ask()
        - Priority 2: Global --non-interactive flag (this method)
        - Priority 3: Auto-detect TTY (fallback)

        Args:
            non_interactive_flag: True if --non-interactive was passed
        """
        if non_interactive_flag:
            self._interactive = False
        else:
            self._interactive = self._tty_available

    def is_interactive(self) -> bool:
        """
        Check if prompts should be shown.

        Returns:
            True if interactive mode is enabled (prompts allowed)
        """
        if self._interactive is None:
            return self._tty_available
        return self._interactive

    @abstractmethod
    def prompt_ask(
        self,
        prompt: str = "",
        choices: Optional[list] = None,
        default: Optional[str] = None,
        force_yes: bool = False,
        required_flag: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        Prompt the user for input with 3-level priority system.

        Priority system:
        1. If force_yes=True → return "yes" (command-specific flags like --force)
        2. If not interactive → return default or raise NonInteractiveError
        3. Else → show prompt (interactive mode)

        Args:
            prompt: Question to ask the user
            choices: List of valid choices (optional)
            default: Default value if non-interactive (required for non-interactive)
            force_yes: Force "yes" response (for command-specific --force/--yes flags)
            required_flag: Flag name to suggest in error message (e.g., "--yes")
                          If set and non-interactive, raises NonInteractiveError with this suggestion
            **kwargs: Implementation-specific prompt arguments

        Returns:
            The user's input as a string

        Raises:
            NonInteractiveError: If non-interactive and no default provided, or if required_flag is set
        """

    @property
    def is_spinner_active(self) -> bool:
        return self._spinner_active

    @property
    @abstractmethod
    def should_stream_docker(self) -> bool:
        """
        Determine if docker output should be streamed based on output context.

        Returns True when docker operations should stream their output (e.g., spinner active in TTY),
        False when operations should run quietly with no intermediate output.

        Returns:
            bool: True to stream docker output, False to suppress it
        """

    @abstractmethod
    def print_data(self, data: Any, **kwargs) -> None:
        """
        Print structured data to stdout (pipeable).

        Use for data that users want to pipe or process:
        - Tables (fm list)
        - JSON output
        - Query results

        Args:
            data: Data to print
            **kwargs: Format-specific options
        """

    @abstractmethod
    def print_status(self, text: str, emoji_code: str = ":zap:", **kwargs) -> None:
        """
        Print status/diagnostic message to stderr.

        Use for:
        - Progress messages
        - Warnings
        - Errors
        - Success confirmations

        Args:
            text: Status message
            emoji_code: Emoji code
            **kwargs: Additional arguments
        """
