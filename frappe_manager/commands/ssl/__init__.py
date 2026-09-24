"""SSL management commands module."""

import typer
from typer_examples import install

ssl_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(ssl_app)

from .acme_sh import acmesh_passthrough
from .add import add_certificate
from .ca import ca_command
from .dns_config import dns_config_command
from .list import list_certificates
from .remove import remove_certificate
from .renew import renew

ssl_app.command(name="renew")(renew)
ssl_app.command(name="list")(list_certificates)
ssl_app.command(name="add")(add_certificate)
ssl_app.command(name="remove")(remove_certificate)
ssl_app.command(name="acme-sh", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})(
    acmesh_passthrough,
)

# dns-config's help lives on the dns_config Typer itself.
ssl_app.add_typer(dns_config_command, name="dns-config")
ssl_app.add_typer(ca_command, name="ca")

__all__ = ["ssl_app"]
