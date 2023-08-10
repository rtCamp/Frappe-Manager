from rich.console import Console
from rich.style import Style
from rich.theme import Theme
from rich.spinner import Spinner
from rich.live import Live

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
        self.current_head = self.previous_head = text
        self.spinner.update(text=self.current_head)
        self.live.start()

    def error(self,text: str):
        self.stderr.print(f":x: {text}")

    def print(self,text: str):
        self.stdout.print(f":white_check_mark: {text}")

    def update_head(self, text: str):
        self.previous_head = self.current_head
        self.current_head = text
        self.live.console.print(self.previous_head)
        self.spinner.update(text=self.current_head)

    def change_head(self, text: str):
        self.previous_head = self.current_head
        self.current_head = text
        self.spinner.update(text=self.current_head)

    def stop(self):
        self.live.stop()

richprint = Richprint()
