"""Maintenance mode commands module."""

import typer
from typer_examples import install

maintenance_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(maintenance_app)

from .disable import disable
from .enable import enable
from .status import status

maintenance_app.command(name="enable")(enable)
maintenance_app.command(name="disable")(disable)
maintenance_app.command(name="status")(status)

__all__ = ["maintenance_app"]
