"""
Rich terminal output handler.

This implementation provides Rich terminal formatting with spinner displays,
live output, and interactive prompts. All functionality is self-contained
within this handler, implementing the OutputHandler interface.
"""

import contextlib
import re
import threading
from collections import deque
from collections.abc import Iterable, Sequence
from typing import Any

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.spinner import Spinner
from rich.text import Text

from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.output_manager.console_singleton import get_stderr_console, get_stdout_console

EMOJI_WORKING = "⚙️"


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

        Prefer the ``spinner`` context manager, which pairs this with its ``stop``; call it
        directly only where the two halves genuinely cannot sit in one scope.

        Args:
            text: The initial status message to display
        """
        with self._lock:
            super().start(text)

            self.current_head = self.previous_head = Text(text=text, style="fm.accent")
            self.spinner = Spinner(text=self.current_head, name="dots2", speed=1)

            self._spinner_active = True
            self._current_text = text

            if self._is_interactive:
                self.live.start(refresh=True)
                self.live.update(self.spinner, refresh=True)
            else:
                self._print_noninteractive_head(text)

    def _print_noninteractive_head(self, text: str) -> None:
        """One ⚙️ line per DISTINCT head in non-interactive mode.

        Deduplicates consecutive repeats (nested spinners/change_head chains
        re-announce the same phase) and suppresses the generic "Working"
        placeholder -- it carries no information and only adds CI noise.
        """
        if text == "Working" or text == getattr(self, "_last_ni_head", None):
            return
        self._last_ni_head = text
        self._emit(self.stderr, f"{EMOJI_WORKING}  {text}")

    def change_head(self, text: str, style: str | None = "fm.accent") -> None:
        """
        Update the current operation status message.

        Args:
            text: The new status message
            style: Rich style/token for the spinner text (honored; None = unstyled)
        """
        if not self._is_interactive:
            self._print_noninteractive_head(text)
            return

        self.previous_head = self.current_head
        self.current_head = text
        if style:
            self.spinner.update(text=Text(self.current_head, style=style))
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
            self._print_noninteractive_head(text)
            return

        self.previous_head = self.current_head
        self.current_head = text
        self.live.console.print(self.previous_head, style="fm.info")
        self.spinner.update(text=Text(self.current_head, style="fm.accent"), style="fm.accent")

    def stop(self) -> None:
        """Stop the spinner so the next write is not corrupted by it.

        A LONE stop is the normal case and not a lesser form of the ``spinner`` context manager:
        31 call sites turn the spinner off before printing pipeable data or handing the terminal
        to a prompt, with no matching start in the same scope to pair it with. A deprecation
        warning used to sit here saying otherwise, behind an env var nothing ever set.
        """
        with self._lock:
            super().stop()

            self._spinner_active = False
            self._current_text = None

            if self._is_interactive:
                self.spinner.update()
                self.live.update(Text("", end=""))
                self.live.stop()

    @contextlib.contextmanager
    def _pause_live(self):
        """Suspend the Live region around a write, then resume it.

        Private on purpose: `_emit` is the only caller, so no call site can forget to pause.
        """
        resume = self._spinner_active and self._is_interactive
        if resume:
            self.live.stop()
        try:
            yield
        finally:
            if resume:
                self.live.start(refresh=True)
                self.live.update(self.spinner, refresh=True)

    def _emit(self, console: Console, renderable, **kwargs) -> None:
        """The ONE door every write goes through. Nothing else may touch a console.

        Pausing the spinner used to be each writer's own responsibility, and seven of the eight
        did not do it -- which is why 31 call sites across the codebase called `output.stop()`
        by hand before printing. With a single door the invariant cannot be violated by
        forgetting: it is not a convention any more, it is the only path to the terminal.

        The lock is here for the same reason: it used to cover `start`/`stop` but none of the
        writers, so a write could interleave with a spinner transition.
        """
        with self._lock, self._pause_live():
            console.print(renderable, **kwargs)

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

        self._emit(self.stderr, msg, **kwargs)

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

        An error ENDS the current operation: any active spinner is stopped
        first, so call sites never need a manual ``output.stop()`` before it.

        Args:
            text: The error message
            emoji_code: Emoji code to display (e.g., ":no_entry:")
        """
        if self._spinner_active:
            self.stop()
        self._emit(self.stderr, f"{emoji_code} {text}")

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
        self.display_error(text, emoji_code)

        # The contract above says ALWAYS. Tolerating a falsy exception here made this handler the
        # only one that returns instead of raising -- JSON and Silent raise unconditionally, so
        # swapping the handler changed whether the program continued past an error.
        raise exception

    def warning(self, text: str, emoji_code: str = ":warning:") -> None:
        """
        Display a warning message.

        Args:
            text: The warning message
            emoji_code: Emoji code to display (e.g., ":warning:")
        """
        self._emit(self.stderr, f"{emoji_code} {text}")

    def live_lines(
        self,
        data: Iterable[tuple[str, bytes]],
        stdout: bool = True,
        stderr: bool = True,
        lines: int = 4,
        padding: tuple[int, int, int, int] = (0, 0, 0, 2),
        stop_string: str | None = None,
        log_prefix: str = "=>",
        line_filters: Sequence[str] | None = None,
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
            line_filters: Case-insensitive substrings whose lines are dropped
                (caller-owned noise knowledge, e.g. docker's progress bars)
        """
        filters = tuple(f.lower() for f in (line_filters or ()))

        if not self._is_interactive:
            while True:
                try:
                    source, line = next(data)
                    if isinstance(line, bytes):
                        line = line.decode(errors="replace")

                    if any(f in line.lower() for f in filters):
                        continue

                    if source == "stdout" and stdout:
                        self._emit(self.stdout, f"{log_prefix} {line.rstrip()}")
                    elif source == "stderr" and stderr:
                        self._emit(self.stderr, f"{log_prefix} {line.rstrip()}")

                    if stop_string and stop_string.lower() in line.lower():
                        break

                except KeyboardInterrupt:
                    break
                except StopIteration:
                    break
            return

        displayed_lines: deque = deque(maxlen=lines)

        while True:
            try:
                source, line = next(data)
                if isinstance(line, bytes):
                    line = line.decode(errors="replace")

                if any(f in line.lower() for f in filters):
                    continue

                if (source == "stdout" and stdout) or (source == "stderr" and stderr):
                    # Build each line's Text ONCE (not per refresh) -- streaming
                    # thousands of lines must not re-wrap the whole buffer each time.
                    rendered = Text(log_prefix + " ", no_wrap=True)
                    rendered.append_text(Text.from_ansi(line))
                    displayed_lines.append(rendered)

                if stop_string and stop_string.lower() in line.lower():
                    raise StopIteration

                # No manual refresh: Live's auto refresh (4fps) throttles rendering
                # regardless of how fast lines arrive.
                self.update_live(Group(*displayed_lines), padding=padding, refresh=False)

            except KeyboardInterrupt:
                # NEVER swallow Ctrl-C mid-stream: clear the tail and propagate.
                self.update_live()
                raise

            except StopIteration:
                self.update_live()
                break

    def update_live(
        self,
        renderable: Any = None,
        padding: tuple[int, int, int, int] = (0, 0, 0, 0),
        refresh: bool = True,
    ) -> None:
        """
        Update the live display with new content.

        Args:
            renderable: Rich renderable object to display
            padding: Padding around content (top, right, bottom, left)
            refresh: Force an immediate render; False defers to Live's auto refresh
        """
        if not self._is_interactive:
            return

        if renderable:
            if padding:
                renderable = Padding(renderable, padding)

            group = Group(self.spinner, renderable)
            self.live.update(group, refresh=False)
        else:
            self.live.update(self.spinner, refresh=False)
        if refresh:
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
                    f"Cannot prompt in non-interactive mode: {prompt}",
                    suggestions=[f"Use: {required_flag}"],
                )
            if default is None:
                suggestions = []
                if choices:
                    suggestions.append(f"Pass one of: {', '.join(choices)}")
                suggestions.append("Run without --non-interactive to enable prompts")
                raise NonInteractiveError(
                    f"Cannot prompt in non-interactive mode: {prompt}",
                    suggestions=suggestions if suggestions else None,
                )
            return default

        prompt_clean = re.sub(r"\[/?[a-z]+\]", "", prompt)

        # No non-interactive branch: the guard above already returned for that case.
        # `is_interactive()` and `_is_interactive` are the same predicate written twice, so an
        # `input()` fallback sat here unreachable, kept alive only by the duplication.
        from InquirerPy import inquirer
        from InquirerPy.utils import InquirerPyStyle

        # Pause the live region only if WE own an active spinner; resume is
        # symmetric -- a prompt with no spinner running must not birth one.
        was_active = self._spinner_active
        resume_text = self._current_text or "Working"
        self.spinner.update()
        if was_active:
            self.live.stop()

        custom_style = InquirerPyStyle(
            {
                "questionmark": "#e5c07b",
                "answered_question": "",
                "answer": "#61afef bold",
                "pointer": "#61afef bold",
                "highlighted": "#61afef bold",
                "selected": "#e5c07b",
            },
        )

        if choices:
            value = inquirer.select(
                message=prompt_clean,
                choices=choices,
                default=default,
                vi_mode=True,
                qmark="",
                amark="",
                style=custom_style,
            ).execute()
        else:
            value = inquirer.text(
                message=prompt_clean,
                default=default or "",
                vi_mode=True,
                qmark="",
                amark="",
                style=custom_style,
            ).execute()

        if was_active:
            self.start(resume_text)
        return value

    def prompt_fuzzy(
        self,
        prompt: str,
        choices: list[str],
        default: str | None = None,
        required_flag: str | None = None,
        **kwargs,
    ) -> str:
        from frappe_manager.exceptions import NonInteractiveError

        if not self.is_interactive():
            if required_flag:
                raise NonInteractiveError(
                    f"Cannot prompt in non-interactive mode: {prompt}",
                    suggestions=[f"Provide: {required_flag}"],
                )
            if default is None:
                raise NonInteractiveError(
                    f"Cannot prompt in non-interactive mode: {prompt}",
                    suggestions=["Run without --non-interactive to enable prompts"],
                )
            return default

        if self._is_interactive:
            from InquirerPy import inquirer

            # Pause the live region only if WE own an active spinner; resume is
            # symmetric -- a prompt with no spinner running must not birth one.
            was_active = self._spinner_active
            resume_text = self._current_text or "Working"
            self.spinner.update()
            if was_active:
                self.live.stop()

            qmark = kwargs.pop("qmark", "🤔")
            amark = kwargs.pop("amark", "🤔")
            vi_mode = kwargs.pop("vi_mode", True)
            mandatory = kwargs.pop("mandatory", True)

            value = inquirer.fuzzy(
                message=prompt,
                choices=choices,
                vi_mode=vi_mode,
                mandatory=mandatory,
                qmark=qmark,
                amark=amark,
                **kwargs,
            ).execute()

            if was_active:
                self.start(resume_text)
            return value
        raise NonInteractiveError(
            f"Cannot prompt in non-interactive mode: {prompt}",
            suggestions=["Run without --non-interactive to enable prompts"],
        )

    @property
    def should_stream_docker(self) -> bool:
        return self._is_interactive and self.is_spinner_active and not self.verbose

    def print_data(self, data: Any, **kwargs) -> None:
        self._print_data_impl(data, **kwargs)

    def data_raw(self, text: str) -> None:
        # No markup, no highlighting, no wrapping: this text is copied and piped, so rich must
        # render it byte-for-byte. `soft_wrap` stops the console breaking a long path mid-token.
        self._emit(self.stdout, text, markup=False, highlight=False, soft_wrap=True)

    def relay(self, text: str, *, stream: str = "stdout") -> None:
        # The child's own stream split is preserved: its stdout is fm's stdout, its stderr is
        # fm's stderr. Same no-markup, no-highlight, no-wrap rendering as data_raw -- this text
        # belongs to another program and fm must not reinterpret it.
        console = self.stdout if stream == "stdout" else self.stderr
        self._emit(console, text, markup=False, highlight=False, soft_wrap=True)

    def _print_data_impl(self, data: Any, **kwargs) -> None:
        import json

        from rich.console import ConsoleRenderable

        # Data on stdout, diagnostics on stderr, so `fm info mybench > file` and `... | jq` work.
        # This used to sit behind FM_STREAM_SEPARATION, defaulting to everything-on-stderr: the
        # piping this method exists for only worked for whoever knew to set the variable.
        #
        # ConsoleRenderable covers Table, Group, Panel, Text, ... -- anything rich
        # can render goes through the console instead of str()'s repr.
        if isinstance(data, ConsoleRenderable):
            self._emit(self.stdout, data)
        elif isinstance(data, (dict, list)):
            json_str = json.dumps(data, indent=2, default=str)
            self._emit(self.stdout, json_str)
        else:
            self._emit(self.stdout, str(data))

    def exit(self, text: str, emoji_code: str = ":no_entry:"):
        """Report a fatal error and end the command.

        Args:
            text: The text to be printed
            emoji_code: The emoji code to be displayed before the text (default: ":no_entry:")
        """
        self.stop()
        self._emit(self.stderr, f"{emoji_code} {text}")
        raise typer.Exit(1)
