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
        self.current_head = self.previous_head = Text(text=text,style='bold blue')
        self.spinner.update(text=self.current_head)
        self.live.start()

    def error(self,text: str,emoji_code: str = ':x:'):
        self.stdout.print(f"{emoji_code} {text}")

    def warning(self,text: str,emoji_code: str = ':warning: '):
        self.stdout.print(f"{emoji_code} {text}")

    def exit(self,text: str,emoji_code: str = ':x:'):
        self.stop()
        self.stdout.print(f"{emoji_code} {text}")
        raise Exit(1)

    def print(self,text: str,emoji_code: str = ':white_check_mark:'):
        self.stdout.print(f"{emoji_code} {text}")

    def update_head(self, text: str):
        self.previous_head = self.current_head
        self.current_head = text
        self.live.console.print(self.previous_head,style='blue')
        self.spinner.update(text=Text(self.current_head,style='blue bold'),style='bold blue')

    def change_head(self, text: str):
        self.previous_head = self.current_head
        self.current_head = text
        self.spinner.update(text=Text(self.current_head,style='blue bold'))
        self.live.refresh()

    def update_live(self,renderable = None, padding: tuple = (0,0,0,0)):
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
        max_height = lines
        displayed_lines = deque(maxlen=max_height)
        while True:
            try:
                source, line = next(data)
                line = line.decode()
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
        self.spinner.update()
        self.live.update(Text('',end=''))
        self.live.stop()

richprint = Richprint()
