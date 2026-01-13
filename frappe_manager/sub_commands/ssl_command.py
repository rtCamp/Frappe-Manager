import typer
from typing import Annotated, Optional
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.site import Bench
from frappe_manager.site_manager.bench_config import DNSProviderConfig
from frappe_manager.site_manager.exceptions import BenchSSLCertificateNotIssued
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES, DNS_PROVIDER
from frappe_manager.ssl_manager.certificate import SSLCertificate, CustomDomainCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateNotDueForRenewalError,
    SSLCertificateNotFoundError,
)
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.exceptions import SSLCertificateError
from frappe_manager.logger.context import LoggerContext
from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.logger import log
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.logging_output import LoggingOutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.metadata_manager import FMConfigManager, FMCloudflareConfig
from rich.table import Table

ssl_root_command = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
dns_config_command = typer.Typer(
    no_args_is_help=True, rich_markup_mode="rich", help="Configure DNS provider credentials for DNS-01 challenge"
)


def get_output_handler(ctx: typer.Context, context: Optional[LoggerContext] = None) -> OutputHandler:
    """
    Get the appropriate output handler based on verbose flag.

    Args:
        ctx: Typer context containing verbose flag
        context: Optional logger context for structured logging

    Returns:
        LoggingOutputHandler wrapping RichOutputHandler with contextual logging
    """
    verbose = ctx.obj.get('verbose', False)

    # Create base handler with verbose setting
    rich = RichOutputHandler(verbose=verbose)

    # Get base logger
    base_logger = log.get_logger()

    # Wrap with context (empty context if not provided)
    contextual_logger = ContextualLogger(base_logger, context)

    # Wrap with logging for automatic file logging
    output = LoggingOutputHandler(rich, contextual_logger)

    return output


@ssl_root_command.command()
def delete(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
):
    """Delete bench ssl certficate."""

    services_manager = ctx.obj["services"]

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="ssl-delete")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    output.change_head("Removing SSL certificate")

    if not bench.has_certificate():
        output.display_error(f"{benchname} doesn't have SSL certificate issued.")
        raise SSLCertificateError("Bench doesn't have SSL certificate issued.", details={"bench": benchname})
    bench.remove_certificate()
    output.print("Removed SSL certificate.", emoji_code=":white_check_mark:")


@ssl_root_command.command()
def renew(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(help="Name of the bench.", autocompletion=sites_autocompletion_callback),
    ] = None,
    domain: Annotated[
        Optional[str],
        typer.Argument(help="Specific domain to renew. If omitted, renews all certificates for the bench."),
    ] = None,
    all: Annotated[bool, typer.Option(help="Renew ssl cert for all benches.")] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Test renewal using Let's Encrypt staging server without modifying the system."),
    ] = False,
):
    """
    Renew SSL certificates.

    Examples:
    - Renew all certificates for a bench: fm ssl renew mysite.local
    - Renew specific domain: fm ssl renew mysite.local www.mysite.local
    - Renew all benches: fm ssl renew --all
    - Test renewal without modifications: fm ssl renew mysite.local --dry-run
    """

    services_manager = ctx.obj["services"]
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager)

    if all:
        sites_list = bench_service.get_bench_names()
    else:
        sites_list = [benchname]

    for benchname in sites_list:
        # Create output handler with context for logging
        context = LoggerContext(bench=benchname, operation="ssl-renew")
        output = get_output_handler(ctx, context=context)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)

        output.change_head(f"Renew certificate for {benchname}")
        try:
            if domain:
                # Validate domain has a certificate configured
                cert_domains = [cert.domain for cert in bench.certificate_manager.certificates]
                if domain not in cert_domains:
                    output.display_error(
                        f"No SSL certificate found for domain '{domain}'.\n"
                        f"Configured certificates: {', '.join(cert_domains) if cert_domains else 'None'}\n"
                        f"To add a certificate, use: fm ssl add {benchname} {domain} --email YOUR_EMAIL"
                    )
                    raise typer.Exit(1)

                # Renew specific domain certificate
                bench.ssl.renew_certificate(domain, dry_run=dry_run)
                if not dry_run:
                    output.print(f"Certificate renewed for {domain}", emoji_code=":white_check_mark:")
            else:
                # Renew all certificates for the bench
                bench.ssl.renew_all_certificates(dry_run=dry_run)
        except (BenchSSLCertificateNotIssued, SSLCertificateNotDueForRenewalError) as e:
            output.warning(e.message)

        except Exception as e:
            output.warning(str(e))


@ssl_root_command.command(name="list")
def list_certificates(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
):
    """List all SSL certificates for a bench."""

    services_manager = ctx.obj["services"]

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="ssl-list")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    # Get all configured domains (primary + aliases)
    all_domains = bench.bench_config.get_all_domains()

    # Get certificate list from manager
    certs = bench.certificate_manager.list_certificates()

    # Create a mapping of domain -> certificate info
    cert_map = {cert['domain']: cert for cert in certs}

    # Create table for display
    table = Table(title=f"SSL Configuration for '{benchname}'", show_header=True, header_style="bold magenta")
    table.add_column("Domain", style="cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Status", style="green")
    table.add_column("Expiry", style="blue")
    table.add_column("Days Left", justify="right")
    table.add_column("Renewal", style="red")

    # Show all domains, whether they have certificates or not
    for domain in all_domains:
        if domain in cert_map:
            # Domain has a certificate configured
            cert = cert_map[domain]
            ssl_type = cert['ssl_type']
            status = "✅ Issued" if cert['exists'] else "❌ Not Issued"

            if cert['exists'] and cert['expiry_date']:
                expiry = cert['expiry_date'].strftime('%Y-%m-%d %H:%M')
                days_left = str(cert['days_until_expiry'])
                renewal = "⚠️ DUE" if cert['needs_renewal'] else "✓ OK"
            else:
                expiry = "N/A"
                days_left = "N/A"
                renewal = "N/A"
        else:
            # Domain has no certificate configured
            ssl_type = "none"
            status = "⚪ No SSL"
            expiry = "N/A"
            days_left = "N/A"
            renewal = "N/A"

        table.add_row(domain, ssl_type, status, expiry, days_left, renewal)

    # Stop the live display before printing the table
    output.stop()
    # Print table using richprint stdout directly since it's structured data
    richprint.stdout.print(table)


@ssl_root_command.command(name="add")
def add_certificate(
    ctx: typer.Context,
    benchname: Annotated[
        str,
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ],
    domain: Annotated[str, typer.Argument(help="Domain name for the certificate")],
    email: Annotated[
        Optional[str],
        typer.Option("--email", "-e", help="Email address for Let's Encrypt notifications"),
    ] = None,
    challenge: Annotated[
        Optional[str],
        typer.Option("--challenge", "-c", help="Challenge type: http01 or dns01 (default: http01)"),
    ] = "http01",
    cname: Annotated[
        Optional[str],
        typer.Option("--cname", help="CNAME delegation record for DNS-01 challenge (requires dns01)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Test certificate generation using Let's Encrypt staging server without adding it to the system.",
        ),
    ] = False,
):
    """
    Add a new SSL certificate for a custom domain.

    Use --dry-run to test certificate generation with Let's Encrypt staging server
    before committing to production. This validates DNS/HTTP configuration without
    rate limits or system modifications.
    """

    services_manager = ctx.obj["services"]

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="ssl-add")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    # Validate domain is configured for this bench
    allowed_domains = bench.bench_config.get_all_domains()
    if domain not in allowed_domains:
        output.display_error(
            f"Domain '{domain}' is not configured for bench '{benchname}'.\n"
            f"Allowed domains: {', '.join(allowed_domains)}\n"
            f"To add an alias domain, use: fm update {benchname} --add-alias {domain}"
        )
        raise typer.Exit(1)

    # Validate inputs
    if not email:
        output.display_error("Email is required for Let's Encrypt certificates. Use --email option.")
        raise typer.Exit(1)

    # Parse challenge type
    if challenge.lower() == "dns01":
        preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
    elif challenge.lower() == "http01":
        preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.http01
    else:
        output.display_error(f"Invalid challenge type: {challenge}. Must be 'http01' or 'dns01'.")
        raise typer.Exit(1)

    # Validate CNAME is only used with DNS-01
    if cname and preferred_challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        output.display_error("CNAME delegation (--cname) can only be used with DNS-01 challenge.")
        raise typer.Exit(1)

    output.change_head(f"Adding SSL certificate for {domain}")

    try:
        # Create certificate object
        if cname:
            # Custom domain with CNAME delegation
            cert = CustomDomainCertificate(
                domain=domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                email=email,
                preferred_challenge=preferred_challenge,
                delegation_cname=cname,
            )
            output.print(f"Using CNAME delegation: {cname}", emoji_code=":information:")
        else:
            # Standard Let's Encrypt certificate
            cert = LetsencryptSSLCertificate(
                domain=domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                email=email,
                preferred_challenge=preferred_challenge,
            )

        # Add certificate via manager
        bench.certificate_manager.add_certificate(cert, dry_run=dry_run)

        if not dry_run:
            output.print(f"SSL certificate added for {domain}", emoji_code=":white_check_mark:")
            output.print("Certificate has been issued and configured.", emoji_code=":zap:")

    except ValueError as e:
        output.display_error(f"Failed to add certificate: {e}")
        raise typer.Exit(1)
    except Exception as e:
        output.display_error(f"Failed to add certificate: {e}")
        output.display_error(f"Error details: {str(e)}")
        raise typer.Exit(1)


@ssl_root_command.command(name="remove")
def remove_certificate(
    ctx: typer.Context,
    benchname: Annotated[
        str,
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ],
    domain: Annotated[str, typer.Argument(help="Domain name of the certificate to remove")],
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force removal without confirmation"),
    ] = False,
):
    """Remove an SSL certificate for a specific domain."""

    services_manager = ctx.obj["services"]

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="ssl-remove")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    # Validate domain has a certificate configured
    cert_domains = [cert.domain for cert in bench.certificate_manager.certificates]
    if domain not in cert_domains:
        output.display_error(
            f"No SSL certificate found for domain '{domain}'.\n"
            f"Configured certificates: {', '.join(cert_domains) if cert_domains else 'None'}"
        )
        raise typer.Exit(1)

    # Confirm removal unless forced
    if not force:
        # Stop live display before showing confirmation prompt
        output.stop()
        confirm = typer.confirm(f"Remove SSL certificate for {domain}?")
        if not confirm:
            output.print("Cancelled.", emoji_code=":x:")
            raise typer.Exit(0)

    output.change_head(f"Removing SSL certificate for {domain}")

    try:
        # Remove certificate via manager
        bench.certificate_manager.remove_certificate_by_domain(domain)

        output.print(f"SSL certificate removed for {domain}", emoji_code=":white_check_mark:")

    except SSLCertificateNotFoundError as e:
        output.display_error(f"Certificate not found: {e}")
        raise typer.Exit(1)
    except Exception as e:
        output.display_error(f"Failed to remove certificate: {e}")
        output.display_error(f"Error details: {str(e)}")
        raise typer.Exit(1)


# ============================================================================
# DNS Provider Configuration Commands
# ============================================================================


@dns_config_command.command(name="cloudflare")
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


# Register dns-config as subcommand of ssl
ssl_root_command.add_typer(dns_config_command, name="dns-config", help="Configure DNS provider credentials")


# ============================================================================
# Helper Functions for DNS Configuration
# ============================================================================


def _show_dns_credentials(ctx: typer.Context, provider_name: str, benchname: Optional[str] = None):
    """Show DNS credentials for a provider."""
    if benchname:
        # Show bench-level config
        services_manager = ctx.obj["services"]
        context = LoggerContext(bench=benchname, operation="dns-config-show")
        output = get_output_handler(ctx, context=context)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)

        if bench.bench_config.dns_providers and provider_name in bench.bench_config.dns_providers:
            config = bench.bench_config.dns_providers[provider_name]
            output.print(f"\n[bold cyan]DNS Credentials for bench '{benchname}':[/bold cyan]", emoji_code="")
            output.print(f"Provider: [green]{provider_name}[/green]", emoji_code="")
            output.print(f"Email: {config.email if config.email else '[dim]Not set[/dim]'}", emoji_code="")
            output.print(
                f"API Token: {'[green]*** (set)[/green]' if config.api_token else '[dim]Not set[/dim]'}", emoji_code=""
            )
            output.print(
                f"API Key: {'[yellow]*** (set)[/yellow]' if config.api_key else '[dim]Not set[/dim]'}", emoji_code=""
            )
        else:
            output.print(
                f"\n[yellow]No {provider_name} credentials configured for bench '{benchname}'[/yellow]",
                emoji_code=":warning:",
            )
            output.print("[dim]Falling back to global configuration (if any)[/dim]", emoji_code="")

    # Show global config (always show, no output handler needed for global info display)
    fm_config = FMConfigManager.import_from_toml()
    richprint.print(f"\n[bold cyan]Global DNS Credentials:[/bold cyan]")
    richprint.print(f"Provider: [green]{provider_name}[/green]")

    if provider_name == DNS_PROVIDER.cloudflare.value:
        richprint.print(f"Email: {fm_config.cloudflare.email if fm_config.cloudflare.email else '[dim]Not set[/dim]'}")
        richprint.print(
            f"API Token: {'[green]*** (set)[/green]' if fm_config.cloudflare.api_token else '[dim]Not set[/dim]'}"
        )
        richprint.print(
            f"API Key: {'[yellow]*** (set)[/yellow]' if fm_config.cloudflare.api_key else '[dim]Not set[/dim]'}"
        )


def _remove_dns_credentials(ctx: typer.Context, provider_name: str, benchname: Optional[str] = None):
    """Remove DNS credentials for a provider."""
    if benchname:
        # Remove bench-level config
        services_manager = ctx.obj["services"]
        context = LoggerContext(bench=benchname, operation="dns-config-remove")
        output = get_output_handler(ctx, context=context)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)

        if bench.bench_config.dns_providers and provider_name in bench.bench_config.dns_providers:
            bench.bench_config.dns_providers.pop(provider_name)
            if not bench.bench_config.dns_providers:
                bench.bench_config.dns_providers = None
            bench.bench_config.export_to_toml(bench.bench_config.root_path)
            output.print(
                f"Removed [green]{provider_name}[/green] credentials for bench '{benchname}'",
                emoji_code=":white_check_mark:",
            )
        else:
            output.warning(f"No {provider_name} credentials configured for bench '{benchname}'")
    else:
        # Remove global config
        fm_config = FMConfigManager.import_from_toml()

        if provider_name == DNS_PROVIDER.cloudflare.value:
            fm_config.cloudflare = FMCloudflareConfig()

        fm_config.export_to_toml()
        richprint.print(f"✅ Removed global [green]{provider_name}[/green] credentials")


def _configure_dns_credentials(
    ctx: typer.Context,
    provider_name: str,
    benchname: Optional[str],
    api_token: Optional[str],
    api_key: Optional[str],
    email: Optional[str],
):
    """Configure DNS credentials for a provider."""
    if benchname:
        # Configure bench-level credentials
        services_manager = ctx.obj["services"]
        context = LoggerContext(bench=benchname, operation="dns-config")
        output = get_output_handler(ctx, context=context)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)

        output.change_head(f"Configuring {provider_name} credentials for bench '{benchname}'")

        # Create or update dns_providers
        if not bench.bench_config.dns_providers:
            bench.bench_config.dns_providers = {}

        bench.bench_config.dns_providers[provider_name] = DNSProviderConfig(
            email=email,
            api_token=api_token,
            api_key=api_key,
        )

        # Save bench config
        bench.bench_config.export_to_toml(bench.bench_config.root_path)

        output.print(
            f"[green]{provider_name}[/green] credentials configured for bench '{benchname}'",
            emoji_code=":white_check_mark:",
        )
        output.print(f"[dim]These credentials will be used for DNS-01 challenges on this bench[/dim]", emoji_code="")
        output.print(f"[dim]Saved to: {bench.bench_config.root_path}[/dim]", emoji_code="")
    else:
        # Configure global credentials (use richprint for global operations)
        richprint.change_head(f"Configuring global {provider_name} credentials")

        fm_config = FMConfigManager.import_from_toml()

        if provider_name == DNS_PROVIDER.cloudflare.value:
            fm_config.cloudflare = FMCloudflareConfig(
                email=email,
                api_token=api_token,
                api_key=api_key,
            )

        # Save global config
        fm_config.export_to_toml()

        richprint.print(f"✅ Global [green]{provider_name}[/green] credentials configured")
        richprint.print("[dim]These credentials will be used by all benches unless overridden at bench level[/dim]")
        richprint.print("[dim]Saved to: ~/frappe/fm_config.toml[/dim]")
