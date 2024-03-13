from rich.console import Console, Group
from rich.style import Style
from rich.theme import Theme
from rich.spinner import Spinner
from rich.live import Live
from rich.text import Text
from rich.padding import Padding
from typer import Exit
from rich.table import Table

import typer
from collections import deque
from typing import Optional

error = Style()
theme = Theme({"errors": error})


class DisplayManager:
    def __init__(self):
        self.stdout = Console()
        # self.stderr = Console(stderr=True)
        self.previous_head = None
        self.current_head = None
        self.spinner = Spinner(text=self.current_head, name="dots2", speed=1)
        self.live = Live(self.spinner, console=self.stdout, transient=True)

    def start(self, text: str):
        """
        Starts the display manager with the given text.

        Args:
            text (str): The text to be displayed.

        Returns:
            None
        """
        self.current_head = self.previous_head = Text(text=text, style="bold blue")
        self.spinner.update(text=self.current_head)
        self.live.start()

    def error(self, text: str,exception: Optional[Exception] = None, emoji_code: str = ":x:"):
        """
        Display an error message with an optional emoji code.

        Args:
            text (str): The error message to display.
            emoji_code (str, optional): The emoji code to display before the error message. Defaults to ':x:'.
        """
        self.stdout.print(f"{emoji_code} {text}")

        if exception:
            raise exception

    def warning(self, text: str, emoji_code: str = ":warning: "):
        """
        Display a warning message with an optional emoji code.

        Args:
            text (str): The warning message to display.
            emoji_code (str, optional): The emoji code to prepend to the message. Defaults to ":warning: ".

        Returns:
            None
        """
        self.stdout.print(f"{emoji_code} {text}")

    def exit(self, text: str, emoji_code: str = ":x:", os_exit=False, error_msg=None):
        """
        Exits the display manager and prints the given text with an optional emoji code and error message.

        Args:
            text (str): The text to be printed.
            emoji_code (str, optional): The emoji code to be displayed before the text. Default is ":x:".
            os_exit (bool, optional): If True, the program will exit with status code 1. Default is False.
            error_msg (str, optional): The error message to be displayed after the text. Default is None.
        """
        self.stop()

        to_print = f"{emoji_code} {text}"
        if error_msg:
            to_print = f"{emoji_code} {text}\n Error : {error_msg}"

        self.stdout.print(to_print)

        if os_exit:
            exit(1)

        raise typer.Exit(1)

    def print(self, text: str, emoji_code: str = ":white_check_mark:", prefix: Optional[str] = None):
        """
        Prints the given text with an optional emoji code.

        Args:
            text (str): The text to be printed.
            emoji_code (str, optional): The emoji code to be displayed before the text. Defaults to ":white_check_mark:".
        """
        msg = f"{emoji_code} {text}"

        if prefix:
            msg = f"{emoji_code} {prefix} {text}"

        self.stdout.print(msg)

    def update_head(self, text: str):
        """
        Update the head of the display manager with the given text.

        Args:
            text (str): The new head text.

        Returns:
            None
        """
        self.previous_head = self.current_head
        self.current_head = text
        self.live.console.print(self.previous_head, style="blue")
        self.spinner.update(
            text=Text(self.current_head, style="blue bold"), style="bold blue"
        )

    def change_head(self, text: str,style: Optional[str] = 'blue bold'):
        """
        Change the head text and update the spinner and live display.

        Args:
            text (str): The new head text.

        Returns:
            None
        """
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
        padding: tuple = (0, 0, 0, 0),
        stop_string: Optional[str] = None,
        log_prefix: str = "=>",
        return_exit_code: bool = False,
        exit_on_failure: bool = False,
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
            exit_on_failure: Whether to exit the program when stop_string is found. Default is False.
        """
        max_height = lines
        displayed_lines = deque(maxlen=max_height)

        while True:
            try:
                source, line = next(data)
                line = line.decode()

                if "[==".lower() in line.lower() or 'Updating files:'.lower() in line.lower():
                    continue

                if source == "stdout" and stdout:
                    displayed_lines.append(line)

                if source == "stderr" and stderr:
                    displayed_lines.append(line)

                if stop_string and stop_string.lower() in line.lower():
                    return 0

                table = Table(show_header=False, box=None)
                table.add_column()

                for linex in list(displayed_lines):
                    table.add_row(Text(f"{log_prefix} {linex.strip()}", style="grey"))

                self.update_live(table, padding=padding)
                self.live.refresh()

            except KeyboardInterrupt as e:
                richprint.live.refresh()

            except StopIteration:
                break

    def stop(self):
        self.spinner.update()
        self.live.update(Text("", end=""))
        self.live.stop()


richprint = DisplayManager()
