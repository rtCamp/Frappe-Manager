"""Helper functions for bench SSL certificate operations."""

from typing import Optional
import typer
from frappe_manager import CLI_BENCHES_DIRECTORY, SSL_RENEW_BEFORE_DAYS
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate, CustomDomainCertificate
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotFoundError
from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import temporary_stop, spinner
from rich.table import Table
from .helpers import get_output_handler


def _add_bench_certificate(
    ctx: typer.Context,
    benchname: str,
    domain: str,
    challenge: LETSENCRYPT_PREFERRED_CHALLENGE,
    cname: Optional[str],
    dry_run: bool,
):
    """Add SSL certificate for a bench domain (existing logic extracted)."""

    services_manager = ctx.obj["services"]

    context = LoggerContext(bench=benchname, operation="ssl-add")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    allowed_domains = bench.bench_config.get_all_domains()
    if domain not in allowed_domains:
        output.display_error(
            f"Domain '{domain}' is not configured for bench '{benchname}'.\n"
            f"Allowed domains: {', '.join(allowed_domains)}\n"
            f"To add an alias domain, use: fm update {benchname} --add-alias {domain}"
        )
        raise typer.Exit(1)

    if cname and challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        output.display_error("CNAME delegation (--cname) can only be used with DNS-01 challenge")
        raise typer.Exit(1)

    output.change_head(f"Adding SSL certificate for {domain}")

    try:
        if cname:
            cert = CustomDomainCertificate(
                domain=domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                api_token=None,
                api_key=None,
                challenge_type=challenge,
                delegation_cname=cname,
            )
            output.print(f"Using CNAME delegation: {cname}", emoji_code=":information:")
        else:
            cert = LetsencryptSSLCertificate(
                domain=domain,
                ssl_type=SUPPORTED_SSL_TYPES.le,
                api_token=None,
                api_key=None,
                challenge_type=challenge,
            )

        with spinner(output, f"Adding SSL certificate for {domain}"):
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


def _remove_bench_certificate(ctx: typer.Context, benchname: str, domain: str, force: bool):
    """Remove SSL certificate for a bench domain (existing logic extracted)."""

    services_manager = ctx.obj["services"]

    context = LoggerContext(bench=benchname, operation="ssl-remove")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    cert_domains = [cert.domain for cert in bench.certificate_manager.certificates]
    if domain not in cert_domains:
        output.display_error(
            f"No SSL certificate found for domain '{domain}'.\n"
            f"Configured certificates: {', '.join(cert_domains) if cert_domains else 'None'}"
        )
        raise typer.Exit(1)

    # Confirm removal unless forced
    if not force:
        with temporary_stop(output):
            choice = output.prompt_ask(
                prompt=f"Remove SSL certificate for {domain}?",
                choices=["yes", "no"],
                default="no",
                required_flag="--force or -f",
            )
        if choice != "yes":
            output.print("Cancelled.", emoji_code=":x:")
            raise typer.Exit(0)

    output.change_head(f"Removing SSL certificate for {domain}")

    try:
        with spinner(output, f"Removing SSL certificate for {domain}"):
            bench.certificate_manager.remove_certificate_by_domain(domain)

        output.print(f"SSL certificate removed for {domain}", emoji_code=":white_check_mark:")

    except SSLCertificateNotFoundError as e:
        output.display_error(f"Certificate not found: {e}")
        raise typer.Exit(1)
    except Exception as e:
        output.display_error(f"Failed to remove certificate: {e}")
        output.display_error(f"Error details: {str(e)}")
        raise typer.Exit(1)


def _list_bench_certificates(ctx: typer.Context, benchname: str):
    """List all SSL certificates for a bench (existing logic extracted)."""

    services_manager = ctx.obj["services"]

    context = LoggerContext(bench=benchname, operation="ssl-list")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    all_domains = bench.bench_config.get_all_domains()

    certs = bench.certificate_manager.list_certificates()

    cert_map = {cert['domain']: cert for cert in certs}

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Domain", style="cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Challenge", style="magenta")
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
            challenge_type = cert.get('challenge_type', 'N/A')
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
            challenge_type = "N/A"
            status = "⚪ No SSL"
            expiry = "N/A"
            days_left = "N/A"
            renewal = "N/A"

        table.add_row(domain, ssl_type, challenge_type, status, expiry, days_left, renewal)

    output.stop()
    output.print_data(table)
