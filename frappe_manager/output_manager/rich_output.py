"""
Rich terminal output handler.

This implementation wraps the existing DisplayManager (richprint) to provide
backward compatibility while implementing the OutputHandler interface.
"""

from collections.abc import Iterable
from typing import Any

from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.output_manager.flags import OutputRefactoringFlags


class RichOutputHandler(OutputHandler):
    """
    Output handler that uses Rich terminal formatting via the existing DisplayManager.

    This handler wraps the existing richprint instance, allowing business logic
    to use the abstract OutputHandler interface while maintaining the current
    CLI behavior.
    """

    def __init__(self, verbose: bool = False):
        """
        Initialize the Rich output handler.

        Args:
            verbose: Show info and debug level messages
        """
        super().__init__(verbose)
        self._richprint = richprint

    def start(self, text: str) -> None:
        """
        Start a new operation with a status message.

        Args:
            text: The initial status message to display
        """
        super().start(text)
        self._richprint.start(text)

    def change_head(self, text: str, style: str | None = None) -> None:
        """
        Update the current operation status message.

        Args:
            text: The new status message
            style: Optional Rich style string (e.g., "blue bold")
        """
        self._richprint.change_head(text, style=style)

    def update_head(self, text: str) -> None:
        """
        Update the head text and print the previous head.

        Args:
            text: The new head text
        """
        self._richprint.update_head(text)

    def stop(self) -> None:
        """
        Stop the current operation status display.
        """
        super().stop()
        self._richprint.stop()

    def print(self, text: str, emoji_code: str = ":zap:", prefix: str | None = None, **kwargs) -> None:
        """
        Print a message with optional emoji and prefix.

        Args:
            text: The message to print
            emoji_code: Emoji code to display (e.g., ":zap:")
            prefix: Optional prefix for the message
            **kwargs: Additional Rich print arguments
        """
        self._richprint.print(text, emoji_code=emoji_code, prefix=prefix, **kwargs)

    def debug(self, text: str, emoji_code: str = ":bug:", **kwargs) -> None:
        """
        Display debug message if verbose mode is enabled.

        Args:
            text: Debug message
            emoji_code: Emoji code to display (e.g., ":bug:")
            **kwargs: Additional Rich print arguments
        """
        if self.verbose:
            self._richprint.print(text, emoji_code=emoji_code, **kwargs)

    def info(self, text: str, emoji_code: str = ":information:", **kwargs) -> None:
        """
        Display info message if verbose mode is enabled.

        Args:
            text: Info message
            emoji_code: Emoji code to display (e.g., ":information:")
            **kwargs: Additional Rich print arguments
        """
        if self.verbose:
            self._richprint.print(text, emoji_code=emoji_code, **kwargs)

    def display_error(self, text: str, emoji_code: str = ":no_entry:") -> None:
        """
        Display error message without raising exception.

        Args:
            text: The error message
            emoji_code: Emoji code to display (e.g., ":no_entry:")
        """
        self._richprint.print(text, emoji_code=emoji_code)

    def error(self, text: str, exception: Exception, emoji_code: str = ":no_entry:") -> None:
        """
        Display an error message and raise the exception.

        This method always raises the provided exception after displaying the error message.
        Use display_error() if you want to display an error without raising an exception.

        Args:
            text: The error message
            exception: Exception to raise after displaying (required)
            emoji_code: Emoji code to display (e.g., ":no_entry:")

        Raises:
            Exception: Always raises the provided exception
        """
        self._richprint.error(text, exception=exception, emoji_code=emoji_code)

    def warning(self, text: str, emoji_code: str = ":warning:") -> None:
        """
        Display a warning message.

        Args:
            text: The warning message
            emoji_code: Emoji code to display (e.g., ":warning:")
        """
        self._richprint.warning(text, emoji_code=emoji_code)

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
            data: Iterator yielding (source, line) tuples where source is "stdout" or "stderr"
            stdout: Whether to display stdout lines
            stderr: Whether to display stderr lines
            lines: Maximum number of lines to display
            padding: Padding around displayed lines (top, right, bottom, left)
            stop_string: String that stops display when found
            log_prefix: Prefix for each line
        """
        self._richprint.live_lines(
            data=data,
            stdout=stdout,
            stderr=stderr,
            lines=lines,
            padding=padding,
            stop_string=stop_string,
            log_prefix=log_prefix,
        )

    def update_live(self, renderable: Any = None, padding: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        """
        Update the live display with new content.

        Args:
            renderable: Rich renderable object to display
            padding: Padding around content (top, right, bottom, left)
        """
        self._richprint.update_live(renderable=renderable, padding=padding)

    def prompt_ask(self, **kwargs) -> str:
        """
        Prompt the user for input.

        Args:
            **kwargs: Arguments passed to Rich Prompt.ask()

        Returns:
            The user's input as a string
        """
        return self._richprint.prompt_ask(**kwargs)

    @property
    def should_stream_docker(self) -> bool:
        return self._richprint._is_tty and self.is_spinner_active and not self.verbose

    def print_data(self, data: Any, **kwargs) -> None:
        import json
        from rich.table import Table as RichTable

        mode = OutputRefactoringFlags.stream_separation_mode()

        if mode == "legacy":
            if isinstance(data, RichTable):
                self._richprint.stderr.print(data)
            else:
                self._richprint.stderr.print(str(data))
        else:
            if isinstance(data, RichTable):
                self._richprint.stdout.print(data)
            elif isinstance(data, (dict, list)):
                json_str = json.dumps(data, indent=2, default=str)
                self._richprint.stdout.print(json_str)
            else:
                self._richprint.stdout.print(str(data))

    def print_status(self, text: str, emoji_code: str = ":zap:", **kwargs) -> None:
        self._richprint.stderr.print(f"{emoji_code} {text}", **kwargs)

    @property
    def is_spinner_active(self) -> bool:
        """
        Check if spinner is currently active by querying the underlying richprint singleton.
        
        This overrides the base class property to check the actual DisplayManager state
        rather than relying on the base class's _spinner_active flag, which would be
        incorrect since RichOutputHandler wraps a shared singleton.
        
        Returns:
            True if the underlying richprint DisplayManager has an active spinner
        """
        return self._richprint.is_spinner_active
