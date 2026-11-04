"""
Silent output handler.

This implementation suppresses all output, useful for testing and
background operations where no user feedback is needed.
"""

from collections.abc import Iterable, Sequence
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

    The terminal stays silent throughout, but a reported failure still reaches fm.log --
    display_error() writes it there, and error() still raises.
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
        super().start(text)

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
        super().stop()

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
        Record a failure to fm.log without printing it or raising. See `OutputHandler.display_error`.

        The terminal stays silent, but an operator left with a bare non-zero exit still needs
        the reason in the log -- a no-op here would erase it twice over, once on screen and
        once in the only place left to look.
        """
        from frappe_manager.logger import get_logger

        get_logger(component="output").error(f"[OUTPUT] {text}")

    def error(self, text: str, exception: Exception, emoji_code: str = ":no_entry:") -> None:
        """
        Re-raise a caught exception, its traceback recorded to fm.log first. See `OutputHandler.error`.

        Raises:
            Exception: always re-raises `exception`.
        """
        import traceback

        from frappe_manager.logger import get_logger

        detail = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
        get_logger(component="output").error(f"[OUTPUT] Exception detail:\n{detail}")
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
        data: Iterable[tuple[str, bytes]],
        stdout: bool = True,
        stderr: bool = True,
        lines: int = 4,
        padding: tuple[int, int, int, int] = (0, 0, 0, 2),
        stop_string: str | None = None,
        log_prefix: str = "=>",
        line_filters: "Sequence[str] | None" = None,
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

    def prompt_ask(
        self,
        prompt: str = "",
        choices: list | None = None,
        default: str | None = None,
        force_yes: bool = False,
        required_flag: str | None = None,
        **kwargs,
    ) -> str:
        from frappe_manager.exceptions import NonInteractiveError

        if force_yes:
            return "yes"

        if required_flag:
            raise NonInteractiveError(
                f"Cannot prompt in non-interactive mode: {prompt}",
                suggestions=[f"Provide: {required_flag}"],
            )

        if default is not None:
            return default

        raise NonInteractiveError(
            f"Cannot prompt in non-interactive mode: {prompt}",
            suggestions=["Run without --non-interactive to enable prompts"],
        )

    def prompt_fuzzy(
        self,
        prompt: str,
        choices: list[str],
        default: str | None = None,
        required_flag: str | None = None,
        **kwargs,
    ) -> str:
        from frappe_manager.exceptions import NonInteractiveError

        if required_flag:
            raise NonInteractiveError(
                f"Cannot prompt in non-interactive mode: {prompt}",
                suggestions=[f"Provide: {required_flag}"],
            )

        if default is not None:
            return default

        raise NonInteractiveError(
            f"Cannot prompt in non-interactive mode: {prompt}",
            suggestions=["Run without --non-interactive to enable prompts"],
        )

    @property
    def should_stream_docker(self) -> bool:
        return False

    def print_data(self, data: Any, **kwargs) -> None:
        pass

