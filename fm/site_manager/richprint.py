from rich.console import Console
from rich.style import Style
from rich.theme import Theme

console = Console()

error = Style()
theme = Theme({
    'errors': error
})

class richprint:
    def __init__(self):
        self.console = Console()
        self.theme = Style()

    def error(self):
        self.console.print()

    def header(self):
        self.console.print()

