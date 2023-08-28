from rich.console import Console
from rich.style import Style
from rich.theme import Theme
from rich.spinner import Spinner
from rich.live import Live
from rich.text import Text
from typer import Exit

error = Style()
theme = Theme({
    'errors': error
})

class Richprint:
    def __init__(self):
        self.stdout = Console()
        self.stderr = Console(stderr=True)
        self.previous_head = None
        self.current_head = None
        self.spinner = Spinner(text=self.current_head, name="dots2", speed=1)
        self.live = Live(self.spinner, console=self.stdout)

    def start(self,text: str):
        self.current_head = self.previous_head = Text(text=text,style='bold blue')
        self.spinner.update(text=self.current_head)
        self.live.start()

    def error(self,text: str):
        self.stderr.print(f"\n:x: {text}")

    def exit(self,text: str):
        self.stop()
        self.stderr.print(f"\n:x: {text}")
        raise Exit(1)

    def print(self,text: str):
        self.stdout.print(f"\n:white_check_mark: {text}")

    def update_head(self, text: str):
        self.previous_head = self.current_head
        self.current_head = text
        self.live.console.print(self.previous_head,style='blue')
        self.spinner.update(text=Text(self.current_head,style='blue bold'),style='bold blue')

    def change_head(self, text: str):
        self.previous_head = self.current_head
        self.current_head = text
        self.spinner.update(text=Text(self.current_head,style='blue bold'))

    def stop(self):
        self.live.stop()

richprint = Richprint()
