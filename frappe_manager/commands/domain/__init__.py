"""Alias domain management commands module."""

import typer
from typer_examples import install

domain_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(domain_app)

from .add import add_domain
from .list import list_domains
from .remove import remove_domain

domain_app.command(name="add")(add_domain)
domain_app.command(name="remove")(remove_domain)
domain_app.command(name="list")(list_domains)

__all__ = ["domain_app"]
