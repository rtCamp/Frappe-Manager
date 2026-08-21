"""DNS configuration subcommand module."""

import typer
from typer_examples import install

dns_config_command = typer.Typer(
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="Store DNS provider credentials for DNS-01 challenges.",
)


# Import and register cloudflare command
from .cloudflare import dns_config_cloudflare

dns_config_command.command(name="cloudflare")(dns_config_cloudflare)

# Ensure examples panel is installed for this sub-typer
install(dns_config_command)
