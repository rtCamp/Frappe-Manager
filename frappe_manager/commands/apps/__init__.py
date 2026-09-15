"""App management commands module."""

import typer
from typer_examples import install

# Create main apps app
apps_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

# Activate typer-examples for this Typer app
install(apps_app)

# Import commands to register them
from .add import add_apps
from .list import list_apps

# Register top-level commands
apps_app.command(name="add")(add_apps)
apps_app.command(name="list")(list_apps)

__all__ = ["apps_app"]
