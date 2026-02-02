"""
Rich terminal output handler.

This implementation provides Rich terminal formatting with spinner displays,
live output, and interactive prompts. All functionality is self-contained
within this handler, implementing the OutputHandler interface.
"""

import re
import sys
import threading
import warnings
from collections import deque
from collections.abc import Iterable, Sequence
from typing import Any

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.spinner import Spinner
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.output_manager.console_singleton import get_stderr_console, get_stdout_console
from frappe_manager.output_manager.flags import OutputRefactoringFlags

# Emoji constants for consistent output
EMOJI_WORKING = "⚙️"
EMOJI_SUCCESS = "⚡"
EMOJI_ERROR = "⛔"
EMOJI_WARNING = "⚠️"
EMOJI_INFO = "ℹ️"

# Cache deprecation flag at module load (performance optimization)
_DEPRECATION_WARNINGS_ENABLED = False
try:
    _DEPRECATION_WARNINGS_ENABLED = OutputRefactoringFlags.use_context_managers()
except ImportError:
    pass


class RichOutputHandler(OutputHandler):
    """
    Output handler that uses Rich terminal formatting.

    This handler provides full Rich functionality including spinners, live displays,
    interactive prompts, and formatted output. It respects both TTY detection and
    the --non-interactive flag for proper behavior in different environments.
    """

    def __init__(self, verbose: bool = False):
        """
        Initialize the Rich output handler.

        Args:
            verbose: Show info and debug level messages
        """
        super().__init__(verbose)

        self.stdout = get_stdout_console()
        self.stderr = get_stderr_console()
        self.previous_head = None
        self.current_head = None

        self.spinner = Spinner(text=Text(""), name="dots2", speed=1)
        self.live = Live(self.spinner, console=self.stderr, transient=True)

        self._spinner_active = False
        self._current_text = None
        self._lock = threading.RLock()

    @property
    def _is_interactive(self) -> bool:
        """
        Check if interactive mode is enabled (considers both TTY and --non-interactive flag).

        Returns:
            True if interactive features (spinners, prompts) should be shown
        """
        if self._interactive is not None:
            return self._interactive
        return self._tty_available

    @property
    def is_spinner_active(self) -> bool:
        """Check if spinner is currently active."""
        return self._spinner_active

    def start(self, text: str) -> None:
        """
        Start a new operation with a status message.

        Args:
            text: The initial status message to display
        """
        with self._lock:
            if _DEPRECATION_WARNINGS_ENABLED:
                warnings.warn(
                    "Direct output.start() is deprecated. Use context managers instead:\n"
                    "    from frappe_manager.output_manager import spinner\n"
                    "    with spinner(output, 'text'): ...\n"
                    "See .plans/output-migration-guide.md for migration guide.",
                    DeprecationWarning,
                    stacklevel=2,
                )

                if OutputRefactoringFlags.strict_mode():
                    raise RuntimeError(
                        "Direct output.start() is not allowed in strict mode. "
                        "Use context managers: with spinner(output, 'text'): ..."
                    )

            super().start(text)

            self.current_head = self.previous_head = Text(text=text, style="bold blue")
            self.spinner = Spinner(text=self.current_head, name="dots2", speed=1)

            self._spinner_active = True
            self._current_text = text

            if self._is_interactive:
                self.live.start(refresh=True)
                self.live.update(self.spinner, refresh=True)
            else:
                self.stderr.print(f"{EMOJI_WORKING}  {text}")

    def change_head(self, text: str, style: str | None = "blue bold") -> None:
        """
        Update the current operation status message.

        Args:
            text: The new status message
            style: Optional Rich style string (e.g., "blue bold")
        """
        if not self._is_interactive:
            self.stderr.print(f"{EMOJI_WORKING}  {text}")
            return

        self.previous_head = self.current_head
        self.current_head = text
        if style:
            self.spinner.update(text=Text(self.current_head, style="blue bold"))
        else:
            self.spinner.update(text=self.current_head)
        self.live.refresh()

    def update_head(self, text: str) -> None:
        """
        Update the head text and print the previous head.

        Args:
            text: The new head text
        """
        if not self._is_interactive:
            self.stderr.print(f"{EMOJI_WORKING}  {text}")
            return

        self.previous_head = self.current_head
        self.current_head = text
        self.live.console.print(self.previous_head, style="blue")
        self.spinner.update(text=Text(self.current_head, style="blue bold"), style="bold blue")

    def stop(self) -> None:
        """Stop the current operation status display."""
        with self._lock:
            if _DEPRECATION_WARNINGS_ENABLED:
                warnings.warn(
                    "Direct output.stop() is deprecated. Use context managers instead:\n"
                    "    from frappe_manager.output_manager import spinner\n"
                    "    with spinner(output, 'text'): ...\n"
                    "See .plans/output-migration-guide.md for migration guide.",
                    DeprecationWarning,
                    stacklevel=2,
                )

                if OutputRefactoringFlags.strict_mode():
                    raise RuntimeError(
                        "Direct output.stop() is not allowed in strict mode. "
                        "Use context managers: with spinner(output, 'text'): ..."
                    )

            super().stop()

            self._spinner_active = False
            self._current_text = None

            if self._is_interactive:
                self.spinner.update()
                self.live.update(Text("", end=""))
                self.live.stop()

    def print(self, text: str, emoji_code: str = ":zap:", prefix: str | None = None, **kwargs) -> None:
        """
        Print a message with optional emoji and prefix.

        Args:
            text: The message to print
            emoji_code: Emoji code to display (e.g., ":zap:")
            prefix: Optional prefix for the message
            **kwargs: Additional Rich print arguments
        """
        if prefix:
            msg = f"{emoji_code} {prefix} {text}"
        else:
            msg = f"{emoji_code} {text}"

        self.stderr.print(msg, **kwargs)

    def debug(self, text: str, emoji_code: str = ":bug:", **kwargs) -> None:
        """
        Display debug message if verbose mode is enabled.

        Args:
            text: Debug message
            emoji_code: Emoji code to display (e.g., ":bug:")
            **kwargs: Additional Rich print arguments
        """
        if self.verbose:
            self.print(text, emoji_code=emoji_code, **kwargs)

    def info(self, text: str, emoji_code: str = ":information:", **kwargs) -> None:
        """
        Display info message if verbose mode is enabled.

        Args:
            text: Info message
            emoji_code: Emoji code to display (e.g., ":information:")
            **kwargs: Additional Rich print arguments
        """
        if self.verbose:
            self.print(text, emoji_code=emoji_code, **kwargs)

    def display_error(self, text: str, emoji_code: str = ":no_entry:") -> None:
        """
        Display error message without raising exception.

        Args:
            text: The error message
            emoji_code: Emoji code to display (e.g., ":no_entry:")
        """
        self.stderr.print(f"{emoji_code} {text}")

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
        self.stderr.print(f"{emoji_code} {text}")

        if exception:
            raise exception

    def warning(self, text: str, emoji_code: str = ":warning:") -> None:
        """
        Display a warning message.

        Args:
            text: The warning message
            emoji_code: Emoji code to display (e.g., ":warning:")
        """
        self.stderr.print(f"{emoji_code} {text}")

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
        if not self._is_interactive:
            while True:
                try:
                    source, line = next(data)
                    line = line.decode()

                    if "[==".lower() in line.lower() or "Updating files:".lower() in line.lower():
                        continue

                    if source == "stdout" and stdout:
                        self.stdout.print(f"{log_prefix} {line.rstrip()}")
                    elif source == "stderr" and stderr:
                        self.stderr.print(f"{log_prefix} {line.rstrip()}")

                    if stop_string and stop_string.lower() in line.lower():
                        break

                except KeyboardInterrupt:
                    break
                except StopIteration:
                    break
            return

        max_height = lines
        displayed_lines = deque(maxlen=max_height)

        # Create table once and reuse (Phase 2 optimization)
        table = Table(show_header=False, box=None)
        table.add_column()

        while True:
            try:
                source, line = next(data)
                line = line.decode()

                if "[==".lower() in line.lower() or "Updating files:".lower() in line.lower():
                    continue

                if source == "stdout" and stdout:
                    displayed_lines.append(line)

                if source == "stderr" and stderr:
                    displayed_lines.append(line)

                if stop_string and stop_string.lower() in line.lower():
                    raise StopIteration

                # Clear rows and rebuild (reuse table object)
                table.rows.clear()

                for linex in list(displayed_lines):
                    prefix_text = Text(log_prefix + " ", no_wrap=True)
                    table_line = Text.from_ansi(linex)
                    prefix_text.append_text(table_line)
                    table.add_row(prefix_text)

                self.update_live(table, padding=padding)
                self.live.refresh()

            except KeyboardInterrupt:
                self.live.refresh()

            except StopIteration:
                self.update_live()
                break

    def update_live(self, renderable: Any = None, padding: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        """
        Update the live display with new content.

        Args:
            renderable: Rich renderable object to display
            padding: Padding around content (top, right, bottom, left)
        """
        if not self._is_interactive:
            return

        if renderable:
            if padding:
                renderable = Padding(renderable, padding)

            group = Group(self.spinner, renderable)
            self.live.update(group)
        else:
            self.live.update(self.spinner)
            self.live.refresh()

    def prompt_ask(
        self,
        prompt: str = "",
        choices: Sequence[str] | None = None,
        default: str | None = None,
        force_yes: bool = False,
        required_flag: str | None = None,
        **kwargs,
    ) -> str:
        from frappe_manager.exceptions import NonInteractiveError

        if force_yes:
            return "yes"

        if not self.is_interactive():
            if required_flag:
                raise NonInteractiveError(
                    f"Cannot prompt in non-interactive mode: {prompt}", suggestions=[f"Use: {required_flag}"]
                )
            if default is None:
                suggestions = []
                if choices:
                    suggestions.append(f"Pass one of: {', '.join(choices)}")
                suggestions.append("Run without --non-interactive to enable prompts")
                raise NonInteractiveError(
                    f"Cannot prompt in non-interactive mode: {prompt}", suggestions=suggestions if suggestions else None
                )
            return default

        prompt_clean = re.sub(r'\[/?[a-z]+\]', '', prompt)

        if self._is_interactive:
            from InquirerPy import inquirer
            from InquirerPy.utils import InquirerPyStyle

            self.spinner.update()
            self.live.stop()

            custom_style = InquirerPyStyle(
                {
                    "questionmark": "#e5c07b",
                    "answered_question": "",
                    "answer": "#61afef bold",
                    "pointer": "#61afef bold",
                    "highlighted": "#61afef bold",
                    "selected": "#e5c07b",
                }
            )

            if choices:
                value = inquirer.select(
                    message=prompt_clean,
                    choices=choices,
                    default=default,
                    vi_mode=True,
                    qmark='',
                    amark='',
                    style=custom_style,
                ).execute()
            else:
                value = inquirer.text(
                    message=prompt_clean,
                    default=default or '',
                    vi_mode=True,
                    qmark='',
                    amark='',
                    style=custom_style,
                ).execute()

            self.start("Working")
            return value
        else:
            if choices:
                choices_str = "/".join(str(c) for c in choices)
                prompt_full = f"{prompt_clean} [{choices_str}]"
                if default:
                    prompt_full += f" (default: {default})"
                prompt_full += ": "

                value = input(prompt_full).strip()
                if not value and default:
                    return default

                if value not in choices:
                    self.stderr.print(f"{EMOJI_WARNING}  Invalid choice '{value}', using default: {default}")
                    return default or choices[0]
                return value
            else:
                prompt_full = prompt_clean
                if default:
                    prompt_full += f" (default: {default})"
                prompt_full += ": "

                value = input(prompt_full).strip()
                return value if value else (default or "")

    @property
    def should_stream_docker(self) -> bool:
        return self._is_interactive and self.is_spinner_active and not self.verbose

    def print_data(self, data: Any, **kwargs) -> None:
        import json
        from rich.table import Table as RichTable

        mode = OutputRefactoringFlags.stream_separation_mode()

        if mode == "legacy":
            if isinstance(data, RichTable):
                self.stderr.print(data)
            else:
                self.stderr.print(str(data))
        else:
            if isinstance(data, RichTable):
                self.stdout.print(data)
            elif isinstance(data, (dict, list)):
                json_str = json.dumps(data, indent=2, default=str)
                self.stdout.print(json_str)
            else:
                self.stdout.print(str(data))

    def print_status(self, text: str, emoji_code: str = ":zap:", **kwargs) -> None:
        self.stderr.print(f"{emoji_code} {text}", **kwargs)

    def exit(self, text: str, emoji_code: str = ":no_entry:", os_exit=False, error_msg=None):
        """
        Exit with error message.

        Args:
            text: The text to be printed
            emoji_code: The emoji code to be displayed before the text (default: ":no_entry:")
            os_exit: If True, the program will exit with status code 1 (default: False)
            error_msg: The error message to be displayed after the text (default: None)
        """
        self.stop()

        to_print = f"{emoji_code} {text}"
        if error_msg:
            to_print = f"{emoji_code} {text}\n Error : {error_msg}"

        self.stderr.print(to_print)

        if os_exit:
            exit(1)

        raise typer.Exit(1)
