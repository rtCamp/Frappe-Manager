"""
Silent output handler.

This implementation suppresses all output, useful for testing and
background operations where no user feedback is needed.
"""

from collections.abc import Iterator
from typing import Any

from frappe_manager.output_manager.base import OutputHandler


class SilentOutputHandler(OutputHandler):
    """
    Output handler that suppresses all output.

    This handler is useful for:
    - Unit testing business logic without output
    - Background operations
    - Automated scripts
    - Performance testing

    All methods are no-ops except error() which still raises exceptions.
    """

    def __init__(self, verbose: bool = False):
        """
        Initialize silent handler (verbose parameter is ignored).

        Args:
            verbose: Ignored (always silent)
        """
        super().__init__(verbose)

    def start(self, text: str) -> None:
        """
        Start a new operation (no-op).

        Args:
            text: The initial status message (ignored)
        """

    def change_head(self, text: str, style: str | None = None) -> None:
        """
        Update the current operation status message (no-op).

        Args:
            text: The new status message (ignored)
            style: Optional style hint (ignored)
        """

    def update_head(self, text: str) -> None:
        """
        Update the head text (no-op).

        Args:
            text: The new head text (ignored)
        """

    def stop(self) -> None:
        """
        Stop the current operation status display (no-op).
        """

    def print(self, text: str, emoji_code: str = ":zap:", prefix: str | None = None, **kwargs) -> None:
        """
        Print a message (no-op).

        Args:
            text: The message to print (ignored)
            emoji_code: Emoji code (ignored)
            prefix: Optional prefix (ignored)
            **kwargs: Additional arguments (ignored)
        """

    def debug(self, text: str, emoji_code: str = ":bug:", **kwargs) -> None:
        """
        Debug message (no-op).

        Args:
            text: Debug message (ignored)
            emoji_code: Emoji code (ignored)
            **kwargs: Additional arguments (ignored)
        """

    def info(self, text: str, emoji_code: str = ":information:", **kwargs) -> None:
        """
        Info message (no-op).

        Args:
            text: Info message (ignored)
            emoji_code: Emoji code (ignored)
            **kwargs: Additional arguments (ignored)
        """

    def display_error(self, text: str, emoji_code: str = ":no_entry:") -> None:
        """
        Display error (no-op).

        Args:
            text: Error message (ignored)
            emoji_code: Emoji code (ignored)
        """

    def error(self, text: str, exception: Exception, emoji_code: str = ":no_entry:") -> None:
        """
        Display an error message and raise the exception.

        This method always raises the provided exception (no-op for display).

        Args:
            text: The error message (ignored)
            exception: Exception to raise (required)
            emoji_code: Emoji code (ignored)
        
        Raises:
            Exception: Always raises the provided exception
        """
        raise exception

    def warning(self, text: str, emoji_code: str = ":warning:") -> None:
        """
        Display a warning message (no-op).

        Args:
            text: The warning message (ignored)
            emoji_code: Emoji code (ignored)
        """

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
        Display live streaming output (consumes iterator but produces no output).

        Args:
            data: Iterator yielding (source, line) tuples
            stdout: Whether to process stdout lines (ignored)
            stderr: Whether to process stderr lines (ignored)
            lines: Maximum number of lines (ignored)
            padding: Padding (ignored)
            stop_string: String that stops iteration when found
            log_prefix: Prefix for each line (ignored)
        """
        # Consume the iterator to prevent blocking, but produce no output
        for _source, line in data:
            if stop_string:
                try:
                    decoded_line = line.decode()
                    if stop_string.lower() in decoded_line.lower():
                        break
                except Exception:
                    pass

    def update_live(self, renderable: Any = None, padding: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        """
        Update the live display (no-op).

        Args:
            renderable: Content to display (ignored)
            padding: Padding (ignored)
        """

    def prompt_ask(self, **kwargs) -> str:
        """
        Prompt the user for input (returns empty string).

        Note: In silent mode, this returns an empty string without prompting.
        This is suitable for testing but not for interactive use.

        Args:
            **kwargs: Prompt arguments (ignored)

        Returns:
            Empty string
        """
        return ""
