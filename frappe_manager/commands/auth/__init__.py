"""Basic auth management commands module."""

import typer
from typer_examples import install

auth_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(auth_app)

from .disable import disable
from .enable import enable
from .status import status

auth_app.command(name="enable")(enable)
auth_app.command(name="disable")(disable)
auth_app.command(name="status")(status)

__all__ = ["auth_app"]
