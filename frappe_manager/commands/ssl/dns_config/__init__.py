"""DNS configuration subcommand module."""

import typer

dns_config_command = typer.Typer(
    no_args_is_help=True, rich_markup_mode="rich", help="Configure DNS provider credentials for DNS-01 challenge"
)


# Import and register cloudflare command
from .cloudflare import dns_config_cloudflare  # noqa: E402

dns_config_command.command(name="cloudflare")(dns_config_cloudflare)
