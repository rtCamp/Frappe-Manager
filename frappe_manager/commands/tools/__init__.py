"""Admin tools management commands module."""

import typer
from typer_examples import install

# Create main tools app
tools_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

# Activate typer-examples for this Typer app
install(tools_app)

# Import commands to register them
from .disable import disable
from .enable import enable
from .status import status

# Register top-level commands
tools_app.command(name="enable")(enable)
tools_app.command(name="disable")(disable)
tools_app.command(name="status")(status)

__all__ = ["tools_app"]
