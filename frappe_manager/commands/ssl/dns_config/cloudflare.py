"""Cloudflare DNS configuration command."""

from typing import Annotated, Optional
import typer
from frappe_manager.ssl_manager import DNS_PROVIDER
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.callbacks import sites_autocompletion_callback
from ..dns_helpers import _show_dns_credentials, _remove_dns_credentials, _configure_dns_credentials


def dns_config_cloudflare(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Bench name for bench-specific credentials. Omit for global configuration.",
            autocompletion=sites_autocompletion_callback,
        ),
    ] = None,
    api_token: Annotated[
        Optional[str],
        typer.Option("--api-token", help="Cloudflare API Token (recommended - scoped permissions)"),
    ] = None,
    api_key: Annotated[
        Optional[str],
        typer.Option("--api-key", help="Cloudflare Global API Key (legacy - full account access)"),
    ] = None,
    email: Annotated[
        Optional[str],
        typer.Option("--email", help="Cloudflare account email (required with Global API Key)"),
    ] = None,
    show: Annotated[
        bool,
        typer.Option("--show", "-s", help="Show current Cloudflare DNS credentials"),
    ] = False,
    remove: Annotated[
        bool,
        typer.Option("--remove", "-r", help="Remove Cloudflare DNS credentials"),
    ] = False,
):
    """
    Configure Cloudflare DNS credentials for DNS-01 challenge.

    Credentials can be configured at two levels:
    - [bold]Global[/bold]: Used by all benches (omit benchname)
    - [bold]Bench-specific[/bold]: Override for a specific bench (provide benchname)

    [bold cyan]Authentication Methods:[/bold cyan]

    1. [green]API Token[/green] (Recommended):
       - More secure with scoped permissions
       - Create at: https://dash.cloudflare.com/profile/api-tokens
       - Template: "Edit zone DNS"
       - Required permission: Zone > DNS > Edit

    2. [yellow]Global API Key[/yellow] (Legacy):
       - Full account access (less secure)
       - Requires --email with your Cloudflare account email
       - Find at: https://dash.cloudflare.com/profile/api-tokens
    """
    provider_name = DNS_PROVIDER.cloudflare.value

    # Show configuration
    if show:
        _show_dns_credentials(ctx, provider_name, benchname)
        return

    # Remove configuration
    if remove:
        _remove_dns_credentials(ctx, provider_name, benchname)
        return

    # Validate Cloudflare-specific credentials
    if not api_token and not api_key:
        richprint.error("Either [bold]--api-token[/bold] or [bold]--api-key[/bold] must be provided")
        richprint.print("\n[green]Recommended:[/green] Use --api-token for better security and scoped permissions")
        richprint.print("[yellow]Legacy:[/yellow] Use --api-key with --email for Global API Key authentication")
        richprint.print("\n[dim]Create API Token at: https://dash.cloudflare.com/profile/api-tokens[/dim]")
        raise typer.Exit(1)

    if api_key and not email:
        richprint.error("[bold]--email[/bold] is required when using [bold]--api-key[/bold] (Global API Key)")
        richprint.print("\n[yellow]Note:[/yellow] API Key authentication requires your Cloudflare account email")
        richprint.print("[green]Better option:[/green] Use --api-token instead (doesn't require email)")
        raise typer.Exit(1)

    # Configure credentials
    _configure_dns_credentials(ctx, provider_name, benchname, api_token, api_key, email)
