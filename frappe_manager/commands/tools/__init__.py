"""Admin tools management commands module."""

import typer
from typer_examples import install

tools_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(tools_app)

from .disable import disable
from .enable import enable
from .status import status

tools_app.command(name="enable")(enable)
tools_app.command(name="disable")(disable)
tools_app.command(name="status")(status)

__all__ = ["tools_app"]
