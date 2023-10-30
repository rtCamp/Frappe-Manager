from rich.console import Console, Group
from rich.style import Style
from rich.theme import Theme
from rich.spinner import Spinner
from rich.live import Live
from rich.text import Text
from rich.padding import Padding
from typer import Exit

from rich.table import Table
from collections import deque
from typing import Optional

error = Style()
theme = Theme({
    'errors': error
})

class Richprint:
    def __init__(self):
        self.stdout = Console()
        # self.stderr = Console(stderr=True)
        self.previous_head = None
        self.current_head = None
        self.spinner = Spinner(text=self.current_head, name="dots2", speed=1)
        self.live = Live(self.spinner, console=self.stdout, transient=True)

    def start(self,text: str):
        """
        The `start` function updates the text of a spinner with a new value and starts a live update.
        
        :param text: The `text` parameter is a string that represents the text that will be displayed in the spinner.
        :type text: str
        """
        self.current_head = self.previous_head = Text(text=text,style='bold blue')
        self.spinner.update(text=self.current_head)
        self.live.start()

    def error(self,text: str,emoji_code: str = ':x:'):
        """
        The function `error` prints a given text with an optional emoji code.
        
        :param text: A string parameter that represents the error message or text that you want to display
        :type text: str
        :param emoji_code: The `emoji_code` parameter is a string that represents an emoji code, defaults to `:x:`.
        :type emoji_code: str (optional)
        """
        self.stdout.print(f"{emoji_code} {text}")

    def warning(self,text: str,emoji_code: str = ':warning: '):
        """
        The function "warning" prints a warning message with an optional emoji code.
        
        :param text: A string that represents the warning message to be displayed
        :type text: str
        :param emoji_code: The `emoji_code` parameter is a string that represents an emoji code,
         defaults to `:warning: `.
        :type emoji_code: str (optional)
        """
        self.stdout.print(f"{emoji_code} {text}")

    def exit(self,text: str,emoji_code: str = ':x:'):
        """
        The `exit` function stops the program, prints a message with an emoji, and exits using `typer.Exit`
        exception.
        
        :param text: The `text` parameter is a string that represents the message or reason for exiting. It
        is the text that will be printed when the `exit` method is called
        :type text: str
        :param emoji_code: The `emoji_code` parameter is a string that represents an emoji code,
         defaults to `:x: `.
        :type emoji_code: str (optional)
        """
        self.stop()
        self.stdout.print(f"{emoji_code} {text}")
        raise Exit(1)

    def print(self,text: str,emoji_code: str = ':white_check_mark:'):
        """
        The function `print` takes in a string `text` and an optional string `emoji_code` and prints the
        `text` with an emoji.
        
        :param text: A string that represents the text you want to print
        :type text: str
        :param emoji_code: The `emoji_code` parameter is a string that represents an emoji code,
         defaults to :white_check_mark:
        :type emoji_code: str (optional)
        """
        self.stdout.print(f"{emoji_code} {text}")

    def update_head(self, text: str):
        """
        The `update_head` function updates text of spinner and print out the prvious text of the
        spinner.

        :param text: The `text` parameter is a string that represents the new value for the head of an
        object
        :type text: str
        """
        self.previous_head = self.current_head
        self.current_head = text
        self.live.console.print(self.previous_head,style='blue')
        self.spinner.update(text=Text(self.current_head,style='blue bold'),style='bold blue')

    def change_head(self, text: str):
        """
        The `change_head` function updates the head of a spinner with the provided text and refreshes the
        display.
        
        :param text: The `text` parameter is a string that represents the new head text that you want to set
        :type text: str
        """
        self.previous_head = self.current_head
        self.current_head = text
        self.spinner.update(text=Text(self.current_head,style='blue bold'))
        self.live.refresh()

    def update_live(self,renderable = None, padding: tuple = (0,0,0,0)):
        """
        The `update_live` function updates the live display with a renderable object, applying padding if
        specified.
        
        :param renderable: The `renderable` parameter is an rich renderable object that can be rendered on the screen by rich. It
        could be an image, text, or any other visual element that you want to display
        :param padding: The `padding` parameter is a tuple that specifies the padding values for the
        `renderable` object. The tuple should contain four values in the order of `(top, right, bottom,
        left)`. These values represent the amount of padding to be added to the `renderable` object on each
        :type padding: tuple
        """
        if padding:
            renderable=Padding(renderable,padding)
        if renderable:
            group = Group(self.spinner,renderable)
            self.live.update(group)
        else:
            self.live.update(self.spinner)
            self.live.refresh()

    def live_lines(
            self,
            data,
            stdout:bool = True,
            stderr:bool = True,
            lines: int = 4,
            padding:tuple = (0,0,0,0),
            return_exit_code:bool = False,
            exit_on_faliure:bool = False,
            stop_string: Optional[str] = None,
            log_prefix: str = '=>',
    ):
        """
        The `live_lines` function takes in various parameters and continuously reads lines from a data
        source, displaying them in a table format with a specified number of lines and optional padding, and
        stops when a specified stop string is encountered.
        
        :param data: The `data` parameter is an iterator that yields tuples of two elements: the first
        element is a string indicating the source of the line (either "stdout" or "stderr"), and the second
        element is the line itself
        :param stdout: A boolean indicating whether to display lines from stdout or not. If set to True,
        lines from stdout will be displayed, defaults to True
        :type stdout: bool (optional)
        :param stderr: A boolean indicating whether to display lines from stderr, defaults to True
        :type stderr: bool (optional)
        :param lines: The `lines` parameter specifies the maximum number of lines to display in the output.
        Only the most recent `lines` lines will be shown, defaults to 4
        :type lines: int (optional)
        :param padding: The `padding` parameter is a tuple that specifies the padding (in characters) to be
        added to the left, right, top, and bottom of the displayed lines in the table. The tuple should have
        four values in the order (left, right, top, bottom). For example, if you
        :type padding: tuple
        :param return_exit_code: The `return_exit_code` parameter is a boolean flag that determines whether
        the `live_lines` function should return the exit code of the process being monitored. If set to
        `True`, the function will return the exit code as an integer value. If set to `False`, the function
        will not return, defaults to False
        :type return_exit_code: bool (optional)
        :param exit_on_faliure: The `exit_on_faliure` parameter is a boolean flag that determines whether
        the function should exit if there is a failure. If set to `True`, the function will exit when a
        failure occurs. If set to `False`, the function will continue running even if there is a failure,
        defaults to False
        :type exit_on_faliure: bool (optional)
        :param stop_string: The `stop_string` parameter is an optional string that can be provided to the
        `live_lines` function. If this string is specified, the function will stop iterating through the
        `data` generator when it encounters a line that contains the `stop_string`
        :type stop_string: Optional[str]
        :param log_prefix: The `log_prefix` parameter is a string that is used as a prefix for each line of
        output displayed in the live view. It is added before the actual line of output and is typically
        used to indicate the source or type of the output (e.g., "=> stdout: This is a line, defaults to =>
        :type log_prefix: str (optional)
        :return: The function does not explicitly return anything. However, it has a parameter
        `return_exit_code` which, if set to `True`, will cause the function to return an exit code of 0 when
        the `stop_string` is found in the output. Otherwise, the function will not return anything.
        """
        max_height = lines
        displayed_lines = deque(maxlen=max_height)
        while True:
            try:
                source, line = next(data)
                line = line.decode()
                if "[==".lower() in line.lower():
                    continue
                if source == 'stdout' and stdout:
                    displayed_lines.append(line)
                if source == 'stderr' and stderr:
                    displayed_lines.append(line)
                if stop_string:
                    if stop_string.lower() in line.lower():
                        return 0
                table = Table(show_header=False,box=None)
                table.add_column()
                for linex in list(displayed_lines):
                    table.add_row(
                        Text(f"{log_prefix} {linex.strip()}",style='grey')
                    )
                self.update_live(table,padding=padding)
                self.live.refresh()
            # except DockerException:
            #     self.update_live()
            #     self.stop()
            #     raise
            except StopIteration:
                break

    def stop(self):
        """
        The function `stop` updates the spinner and live output, and then stops the live output.
        """
        self.spinner.update()
        self.live.update(Text('',end=''))
        self.live.stop()

richprint = Richprint()
