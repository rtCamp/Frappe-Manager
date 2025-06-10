import sys
from typing import Optional, Any
import typer
from rich.console import Console
from rich.theme import Theme
from rich.tree import Tree
from rich.table import Table
from rich.panel import Panel

custom_theme = Theme({
    "info": "dim cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "green",
    "heading": "bold cyan",
    "highlight": "bold magenta",
    "dimmed": "dim"
})

class DisplayManager:
    """Handles all console output using Rich."""

    def __init__(self, verbose: bool = False):
        self._console = Console(theme=custom_theme, stderr=True, highlight=False) # Default to stderr for logs/status
        self._stdout_console = Console(theme=custom_theme, highlight=False) # For primary command output if needed
        self.verbose = verbose

    def print(self, message: Any = "", **kwargs):
        """Basic print, usually to stdout."""
        self._stdout_console.print(message, **kwargs)

    def info(self, message: str, **kwargs):
        """Print informational messages (dimmed), respects verbosity."""
        if self.verbose:
            self._console.print(f"[info]{message}[/info]", **kwargs)

    def dimmed(self, message: str, **kwargs):
        """Print dimmed messages, always shown."""
        self._console.print(f"[dimmed]{message}[/dimmed]", **kwargs)

    def success(self, message: str, **kwargs):
        """Print success messages."""
        self._console.print(f"[success]:heavy_check_mark: {message}[/success]", **kwargs)

    def warning(self, message: str, **kwargs):
        """Print warning messages."""
        self._console.print(f"[warning]:warning: {message}[/warning]", **kwargs)

    def error(self, message: str, exit_code: Optional[int] = None, prefix: bool = True, **kwargs):
        """Print error messages and optionally exit."""
        if prefix:
            self._console.print(f"[error]Error: {message}[/error]", **kwargs)
        else:
            self._console.print(f"[error]{message}[/error]", **kwargs)
        if exit_code is not None:
            raise typer.Exit(code=exit_code)

    def heading(self, message: str, **kwargs):
        """Print section headings."""
        self._console.print(f"\n[heading]{message}[/heading]", **kwargs)

    def highlight(self, text: str) -> str:
        """Applies highlight style for inline use."""
        return f"[highlight]{text}[/highlight]"

    def display_tree(self, tree: Tree, **kwargs):
        """Prints a rich Tree object."""
        self._stdout_console.print(tree, **kwargs)

    def display_table(self, table: Table, **kwargs):
         """Prints a rich Table object."""
         self._stdout_console.print(table, **kwargs)

    def display_panel(self, panel: Panel, **kwargs):
         """Prints a rich Panel object."""
         self._stdout_console.print(panel, **kwargs)

display = DisplayManager()
