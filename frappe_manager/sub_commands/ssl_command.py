import typer
from typing import Annotated, Optional
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.site import Bench
from frappe_manager.site_manager.exceptions import BenchSSLCertificateNotIssued
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
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
from rich.table import Table

ssl_root_command = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")


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

    richprint.change_head("Removing SSL certificate")

    if not bench.has_certificate():
        richprint.error(f"{benchname} doesn't have SSL certificate issued.")
        raise SSLCertificateError("Bench doesn't have SSL certificate issued.", details={"bench": benchname})
    bench.remove_certificate()
    richprint.print("Removed SSL certificate.")


@ssl_root_command.command()
def renew(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(help="Name of the bench.", autocompletion=sites_autocompletion_callback),
    ] = None,
    all: Annotated[bool, typer.Option(help="Renew ssl cert for all benches.")] = False,
):
    """Renew bench ssl certficate."""

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

        richprint.change_head("Renew certificate")
        try:
            bench.renew_certificate()
        except (BenchSSLCertificateNotIssued, SSLCertificateNotDueForRenewalError) as e:
            richprint.warning(e.message)

        except Exception as e:
            richprint.warning(str(e))


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

    # Get certificate list from manager
    certs = bench.certificate_manager.list_certificates()

    if not certs:
        richprint.print(f"No SSL certificates configured for bench '{benchname}'")
        return

    # Create table for display
    table = Table(title=f"SSL Certificates for '{benchname}'", show_header=True, header_style="bold magenta")
    table.add_column("Domain", style="cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Status", style="green")
    table.add_column("Expiry", style="blue")
    table.add_column("Days Left", justify="right")
    table.add_column("Renewal", style="red")

    for cert in certs:
        domain = cert['domain']
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

        table.add_row(domain, ssl_type, status, expiry, days_left, renewal)

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
):
    """Add a new SSL certificate for a custom domain."""

    services_manager = ctx.obj["services"]

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="ssl-add")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    # Validate inputs
    if not email:
        richprint.error("Email is required for Let's Encrypt certificates. Use --email option.")
        raise typer.Exit(1)

    # Parse challenge type
    if challenge.lower() == "dns01":
        preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
    elif challenge.lower() == "http01":
        preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.http01
    else:
        richprint.error(f"Invalid challenge type: {challenge}. Must be 'http01' or 'dns01'.")
        raise typer.Exit(1)

    # Validate CNAME is only used with DNS-01
    if cname and preferred_challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        richprint.error("CNAME delegation (--cname) can only be used with DNS-01 challenge.")
        raise typer.Exit(1)

    richprint.change_head(f"Adding SSL certificate for {domain}")

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
            richprint.print(f"Using CNAME delegation: {cname}")
        else:
            # Standard Let's Encrypt certificate
            cert = LetsencryptSSLCertificate(
                domain=domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                email=email,
                preferred_challenge=preferred_challenge,
            )

        # Add certificate via manager
        bench.certificate_manager.add_certificate(cert)

        richprint.print(f"✅ SSL certificate added for {domain}")
        richprint.print("Certificate has been issued and configured.")

    except ValueError as e:
        richprint.error(f"Failed to add certificate: {e}")
        raise typer.Exit(1)
    except Exception as e:
        richprint.error(f"Failed to add certificate: {e}")
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

    # Confirm removal unless forced
    if not force:
        confirm = typer.confirm(f"Remove SSL certificate for {domain}?")
        if not confirm:
            richprint.print("Cancelled.")
            raise typer.Exit(0)

    richprint.change_head(f"Removing SSL certificate for {domain}")

    try:
        # Remove certificate via manager
        bench.certificate_manager.remove_certificate_by_domain(domain)

        richprint.print(f"✅ SSL certificate removed for {domain}")

    except SSLCertificateNotFoundError as e:
        richprint.error(f"Certificate not found: {e}")
        raise typer.Exit(1)
    except Exception as e:
        richprint.error(f"Failed to remove certificate: {e}")
        output.display_error(f"Error details: {str(e)}")
        raise typer.Exit(1)
