from collections import deque
import sys
import threading
import warnings

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.spinner import Spinner
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

error = Style()
theme = Theme({"errors": error})


class DisplayManager:
    def __init__(self):
        self.stdout = Console()
        self.stderr = Console(stderr=True)
        self.previous_head = None
        self.current_head = None

        # Detect TTY for interactive features
        self._is_tty = sys.stdin.isatty() and sys.stdout.isatty()

        self.spinner = Spinner(text=Text(""), name="dots2", speed=1)
        # Use stderr for Live display so it coordinates with logging output
        self.live = Live(self.spinner, console=self.stderr, transient=True)

        # Track spinner state for context managers
        self._spinner_active = False
        self._current_text = None

        # Thread-safety lock for spinner state operations
        self._lock = threading.RLock()

    @property
    def is_spinner_active(self) -> bool:
        """Check if spinner is currently active."""
        return self._spinner_active

    def start(self, text: str):
        """
        Starts the display manager with the given text.

        DEPRECATION WARNING: Direct use of richprint.start() is deprecated.
        Use context managers instead:

            from frappe_manager.output_manager import spinner
            with spinner(output, "Working..."):
                do_work()

        See: .plans/output-migration-guide.md

        Args:
            text (str): The text to be displayed.

        Returns:
            None
        """
        with self._lock:
            # Import flags here to avoid circular dependency
            try:
                from frappe_manager.output_manager.flags import OutputRefactoringFlags

                if OutputRefactoringFlags.use_context_managers():
                    warnings.warn(
                        "Direct richprint.start() is deprecated. Use context managers instead:\n"
                        "    from frappe_manager.output_manager import spinner\n"
                        "    with spinner(output, 'text'): ...\n"
                        "See .plans/output-migration-guide.md for migration guide.",
                        DeprecationWarning,
                        stacklevel=2,
                    )

                    if OutputRefactoringFlags.strict_mode():
                        raise RuntimeError(
                            "Direct richprint.start() is not allowed in strict mode. "
                            "Use context managers: with spinner(output, 'text'): ..."
                        )
            except ImportError:
                pass

            self.current_head = self.previous_head = Text(text=text, style="bold blue")
            self.spinner = Spinner(text=self.current_head, name="dots2", speed=1)

            self._spinner_active = True
            self._current_text = text

            if self._is_tty:
                self.live.start(refresh=True)
                self.live.update(self.spinner, refresh=True)
            else:
                self.stderr.print(f"⚙️  {text}")

    def error(self, text: str, exception: Exception | None = None, emoji_code: str = ":no_entry:"):
        """
        Display an error message with an optional emoji code.
        Uses stderr to coordinate with Live display.

        Args:
            text (str): The error message to display.
            exception (Exception | None): Optional exception to raise after displaying.
            emoji_code (str, optional): The emoji code to display before the error message. Defaults to ':no_entry:'.
        """
        # Use stderr console to coordinate with Live display
        self.stderr.print(f"{emoji_code} {text}")

        if exception:
            raise exception

    def warning(self, text: str, emoji_code: str = ":warning: "):
        """
        Display a warning message with an optional emoji code.
        Uses stderr to coordinate with Live display.

        Args:
            text (str): The warning message to display.
            emoji_code (str, optional): The emoji code to prepend to the message. Defaults to ":warning: ".

        Returns:
            None
        """
        # Use stderr console to coordinate with Live display
        self.stderr.print(f"{emoji_code} {text}")

    def exit(self, text: str, emoji_code: str = ":no_entry:", os_exit=False, error_msg=None):
        """
        Exits the display manager and prints the given text with an optional emoji code and error message.
        Uses stderr to coordinate with Live display.

        Args:
            text (str): The text to be printed.
            emoji_code (str, optional): The emoji code to be displayed before the text. Default is ":no_entry:".
            os_exit (bool, optional): If True, the program will exit with status code 1. Default is False.
            error_msg (str, optional): The error message to be displayed after the text. Default is None.
        """
        self.stop()

        to_print = f"{emoji_code} {text}"
        if error_msg:
            to_print = f"{emoji_code} {text}\n Error : {error_msg}"

        # Use stderr console to coordinate with Live display
        self.stderr.print(to_print)

        if os_exit:
            exit(1)

        raise typer.Exit(1)

    def print(self, text: str, emoji_code: str = ":zap:", prefix: str | None = None, **kwargs):
        """
        Prints the given text with an optional emoji code.
        Uses stderr to coordinate with Live display spinner.

        Args:
            text (str): The text to be printed.
            emoji_code (str, optional): The emoji code to be displayed before the text. Defaults to ":zap:".
            prefix (str, optional): Optional prefix to add before the text.
        """
        msg = f"{emoji_code} {text}"

        if prefix:
            msg = f"{emoji_code} {prefix} {text}"

        # Use stderr console to coordinate with Live display
        self.stderr.print(msg, **kwargs)

    def update_head(self, text: str):
        """
        Update the head of the display manager with the given text.

        Args:
            text (str): The new head text.

        Returns:
            None
        """
        if not self._is_tty:
            self.stderr.print(f"⚙️  {text}")
            return

        self.previous_head = self.current_head
        self.current_head = text
        self.live.console.print(self.previous_head, style="blue")
        self.spinner.update(text=Text(self.current_head, style="blue bold"), style="bold blue")

    def prompt_ask(self, **args):
        import re

        prompt = args.get('prompt', 'Enter value')
        choices = args.get('choices')
        default = args.get('default')

        prompt_clean = re.sub(r'\[/?[a-z]+\]', '', prompt)

        if self._is_tty:
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
                    self.stderr.print(f"⚠️  Invalid choice '{value}', using default: {default}")
                    return default or choices[0]
                return value
            else:
                prompt_full = prompt_clean
                if default:
                    prompt_full += f" (default: {default})"
                prompt_full += ": "

                value = input(prompt_full).strip()
                return value if value else (default or "")

    def change_head(self, text: str, style: str | None = "blue bold"):
        """
        Change the head text and update the spinner and live display.

        Args:
            text (str): The new head text.

        Returns:
            None
        """
        if not self._is_tty:
            self.stderr.print(f"⚙️  {text}")
            return

        self.previous_head = self.current_head
        self.current_head = text
        if style:
            self.spinner.update(text=Text(self.current_head, style="blue bold"))
        else:
            self.spinner.update(text=self.current_head)
        self.live.refresh()

    def update_live(self, renderable=None, padding: tuple = (0, 0, 0, 0)):
        """
        Update the live display with the given renderable object and padding.

        Args:
            renderable: The object to be rendered on the live display.
            padding: The padding values for the renderable object (top, right, bottom, left).
        """
        if not self._is_tty:
            return

        if renderable:
            if padding:
                renderable = Padding(renderable, padding)

            group = Group(self.spinner, renderable)
            self.live.update(group)
        else:
            self.live.update(self.spinner)
            self.live.refresh()

    def live_lines(
        self,
        data,
        stdout: bool = True,
        stderr: bool = True,
        lines: int = 4,
        padding: tuple = (0, 0, 0, 2),
        stop_string: str | None = None,
        log_prefix: str = "=>",
    ):
        """
        Display live lines from the given data source.

        Args:
            data: An iterator that yields tuples of (source, line) where source is either "stdout" or "stderr" and line is a string.
            stdout: Whether to display lines from the stdout source. Default is True.
            stderr: Whether to display lines from the stderr source. Default is True.
            lines: The maximum number of lines to display. Default is 4.
            padding: A tuple of four integers representing the padding (top, right, bottom, left) around the displayed lines. Default is (0, 0, 0, 0).
            stop_string: A string that, if found in a line, will stop the display and return 0. Default is None.
            log_prefix: The prefix to add to each displayed line. Default is "=>".
            return_exit_code: Whether to return the exit code when stop_string is found. Default is False.
        """
        if not self._is_tty:
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

        while True:
            try:
                source, line = next(data)
                line = line.decode()
                # print(' --',line)

                if "[==".lower() in line.lower() or "Updating files:".lower() in line.lower():
                    continue

                if source == "stdout" and stdout:
                    displayed_lines.append(line)

                if source == "stderr" and stderr:
                    displayed_lines.append(line)

                if stop_string and stop_string.lower() in line.lower():
                    raise StopIteration

                table = Table(show_header=False, box=None)
                table.add_column()

                for linex in list(displayed_lines):
                    prefix_text = Text(log_prefix + " ", no_wrap=True)
                    table_line = Text.from_ansi(linex)
                    prefix_text.append_text(table_line)
                    table.add_row(prefix_text)

                self.update_live(table, padding=padding)
                self.live.refresh()

            except KeyboardInterrupt:
                richprint.live.refresh()

            except StopIteration:
                self.update_live()
                break

    def stop(self):
        """
        Stops the display manager spinner.

        DEPRECATION WARNING: Direct use of richprint.stop() is deprecated.
        Use context managers instead:

            from frappe_manager.output_manager import spinner
            with spinner(output, "Working..."):
                do_work()  # Spinner automatically stops when exiting context

        See: .plans/output-migration-guide.md

        Returns:
            None
        """
        with self._lock:
            # Import flags here to avoid circular dependency
            try:
                from frappe_manager.output_manager.flags import OutputRefactoringFlags

                if OutputRefactoringFlags.use_context_managers():
                    warnings.warn(
                        "Direct richprint.stop() is deprecated. Use context managers instead:\n"
                        "    from frappe_manager.output_manager import spinner\n"
                        "    with spinner(output, 'text'): ...\n"
                        "See .plans/output-migration-guide.md for migration guide.",
                        DeprecationWarning,
                        stacklevel=2,
                    )

                    if OutputRefactoringFlags.strict_mode():
                        raise RuntimeError(
                            "Direct richprint.stop() is not allowed in strict mode. "
                            "Use context managers: with spinner(output, 'text'): ..."
                        )
            except ImportError:
                pass

            self._spinner_active = False
            self._current_text = None

            if self._is_tty:
                self.spinner.update()
                self.live.update(Text("", end=""))
                self.live.stop()


richprint = DisplayManager()
