"""`fm ssl ca`: the dev CA's host trust lifecycle."""

import typer

# Aliased: this package has its own `install` submodule, and importing it below binds the name
# `install` on the package, shadowing typer_examples' function.
from typer_examples import install as install_examples

ca_command = typer.Typer(
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="Inspect, install or remove fm's dev CA in this host's trust stores.",
)


from .install import ca_install
from .remove import ca_remove
from .status import ca_status

ca_command.command(name="status")(ca_status)
ca_command.command(name="install")(ca_install)
ca_command.command(name="remove")(ca_remove)

install_examples(ca_command)
