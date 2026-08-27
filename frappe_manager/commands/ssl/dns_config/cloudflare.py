"""Cloudflare DNS configuration command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.ssl_manager import DNS_PROVIDER
from frappe_manager.utils.callbacks import sites_autocompletion_callback

from ..dns_helpers import _configure_dns_credentials, _remove_dns_credentials, _show_dns_credentials


@example(
    "Store a global API token",
    "--api-token cf_AbCdEf1234567890",
)
@example(
    "Store a second account under a label",
    "--api-token cf_ZyXwVu0987654321 --name acct-b",
    detail="Labelled sets go to [ssl.dns_providers.acct-b]; bind one with fm ssl add BENCH DOMAIN --challenge dns01 --dns-provider acct-b.",
)
@example(
    "Override the token for one bench",
    "{benchname} --api-token cf_ZyXwVu0987654321",
    benchname="mybench",
)
@example(
    "Store a labelled set for one bench only",
    "{benchname} --api-token cf_QqRrSs1122334455 --name acct-b",
    benchname="mybench",
)
@example(
    "Use a legacy Global API Key instead",
    "--api-key 1234567890abcdef1234 --email admin@example.com",
)
@example(
    "Show what is stored",
    "--show",
    detail="Lists every labelled set at both scopes, secrets masked. With a bench name, prints that bench's sets as well as the global ones, and --name narrows to one label.",
)
@example(
    "Drop one labelled set",
    "--remove --name acct-b",
    detail="Without --name, a scope holding more than one set is refused rather than guessed at.",
)
def dns_config_cloudflare(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Bench to configure. Omit for global credentials.",
            autocompletion=sites_autocompletion_callback,
        ),
    ] = None,
    api_token: Annotated[
        str | None,
        typer.Option("--api-token", help="Cloudflare API token, scoped to the zones you issue for."),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Legacy Global API Key, which grants full account access. Requires --email."),
    ] = None,
    email: Annotated[
        str | None,
        typer.Option("--email", help="Cloudflare account email. Required with --api-key only."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            "-n",
            help="Label for this credential set, e.g. an account name. Omit for the default account.",
        ),
    ] = None,
    show: Annotated[
        bool,
        typer.Option("--show", "-s", help="Print the stored credentials, secrets masked. Writes nothing."),
    ] = False,
    remove: Annotated[
        bool,
        typer.Option("--remove", "-r", help="Delete the stored credentials."),
    ] = False,
):
    """
    Store Cloudflare API credentials for DNS-01 certificate issuance.

    Credentials are global; pass a bench name to override them for that bench alone. An API token needs Zone > DNS > Edit, created at https://dash.cloudflare.com/profile/api-tokens

    A --name stores the credentials as a labelled set, so one host or bench can hold several Cloudflare accounts (or several least-privilege tokens) at once. A certificate picks one with fm ssl add --dns-provider LABEL; certificates that name no label keep using the unlabelled default.
    """
    provider_name = DNS_PROVIDER.cloudflare.value

    # Show configuration
    if show:
        _show_dns_credentials(ctx, provider_name, benchname, name)
        return

    # Remove configuration
    if remove:
        _remove_dns_credentials(ctx, provider_name, benchname, name)
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
    _configure_dns_credentials(ctx, provider_name, benchname, api_token, api_key, email, name)
