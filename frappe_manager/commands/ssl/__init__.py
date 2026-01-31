"""SSL management commands module."""

import typer

# Create main SSL app
ssl_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

# Import commands to register them
from .renew import renew  # noqa: E402
from .list import list_certificates  # noqa: E402
from .add import add_certificate  # noqa: E402
from .remove import remove_certificate  # noqa: E402
from .acme_sh import acmesh_passthrough  # noqa: E402
from .dns_config import dns_config_command  # noqa: E402

# Register top-level commands
ssl_app.command(name="renew")(renew)
ssl_app.command(name="list")(list_certificates)
ssl_app.command(name="add")(add_certificate)
ssl_app.command(name="remove")(remove_certificate)
ssl_app.command(name="acme-sh", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})(
    acmesh_passthrough
)

# Register subcommand Typer app
ssl_app.add_typer(dns_config_command, name="dns-config", help="Configure DNS provider credentials")

__all__ = ["ssl_app"]
