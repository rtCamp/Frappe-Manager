"""App management commands module."""

import typer
from typer_examples import install

apps_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(apps_app)

from .add import add_apps
from .list import list_apps

apps_app.command(name="add")(add_apps)
apps_app.command(name="list")(list_apps)

__all__ = ["apps_app"]
