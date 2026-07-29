"""Cloudflare DNS configuration command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.ssl_manager import DNS_PROVIDER
from frappe_manager.utils.callbacks import sites_autocompletion_callback

from ..dns_helpers import _configure_dns_credentials, _remove_dns_credentials, _show_dns_credentials


@example(
    "Configure global Cloudflare credentials using API Token (recommended)",
    "--api-token YOUR_CLOUDFLARE_API_TOKEN",
    detail="Stores a global Cloudflare API token for DNS-01 challenges. Recommended for scoped permissions.",
)
@example(
    "Configure global Cloudflare credentials using API Key (legacy)",
    "--api-key YOUR_API_KEY --email admin@example.com",
    detail="Stores legacy Global API Key credentials; less secure and requires account email.",
)
@example(
    "Configure bench-specific Cloudflare credentials (overrides global)",
    "--api-token BENCH_SPECIFIC_TOKEN",
    detail="Sets Cloudflare credentials for a specific bench, overriding global configuration.",
)
@example(
    "Show global Cloudflare DNS credentials configuration",
    "--show",
    detail="Displays stored global Cloudflare credentials (if any).",
)
@example(
    "Show bench-specific Cloudflare DNS credentials",
    "--show",
    detail="Displays stored Cloudflare credentials for the specified bench.",
)
@example(
    "Remove global Cloudflare DNS credentials",
    "--remove",
    detail="Removes global Cloudflare credential configuration.",
)
@example(
    "Remove bench-specific Cloudflare DNS credentials",
    "--remove",
    detail="Removes Cloudflare credential configuration for the specified bench.",
)
def dns_config_cloudflare(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Bench name for bench-specific credentials. Omit for global configuration.",
            autocompletion=sites_autocompletion_callback,
        ),
    ] = None,
    api_token: Annotated[
        str | None,
        typer.Option("--api-token", help="Cloudflare API Token (recommended; scoped permissions)"),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Cloudflare Global API Key (legacy; full account access)"),
    ] = None,
    email: Annotated[
        str | None,
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
        output = get_global_output_handler()
        output.display_error("Either [bold]--api-token[/bold] or [bold]--api-key[/bold] must be provided")
        output.print("\n[fm.ok]Recommended:[/fm.ok] Use --api-token for better security and scoped permissions")
        output.print("[fm.warn]Legacy:[/fm.warn] Use --api-key with --email for Global API Key authentication")
        output.print("\n[fm.muted]Create API Token at: https://dash.cloudflare.com/profile/api-tokens[/fm.muted]")
        raise typer.Exit(1)

    if api_key and not email:
        output = get_global_output_handler()
        output.display_error("[bold]--email[/bold] is required when using [bold]--api-key[/bold] (Global API Key)")
        output.print("\n[fm.warn]Note:[/fm.warn] API Key authentication requires your Cloudflare account email")
        output.print("[fm.ok]Better option:[/fm.ok] Use --api-token instead (doesn't require email)")
        raise typer.Exit(1)

    # Configure credentials
    _configure_dns_credentials(ctx, provider_name, benchname, api_token, api_key, email)
