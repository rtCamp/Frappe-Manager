"""Helper functions for bench SSL certificate operations."""

from typing import TYPE_CHECKING

import typer
from rich.table import Table

from frappe_manager.output_manager import spinner
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateNotFoundError,
    SSLDNSProviderNotConfigured,
)
from frappe_manager.ssl_manager.letsencrypt_certificate import build_letsencrypt_certificate
from frappe_manager.ssl_manager.ssl_utils import resolve_dns_provider

from .helpers import get_output_handler

if TYPE_CHECKING:
    from frappe_manager.site_manager.bench_config import BenchConfig


def _add_bench_certificate(
    ctx: typer.Context,
    benchname: str,
    domain: str,
    challenge: LETSENCRYPT_PREFERRED_CHALLENGE,
    cname: str | None,
    dry_run: bool,
    dev: bool = False,
    dns_provider: str | None = None,
):
    """Add SSL certificate for a bench domain (existing logic extracted)."""

    services_manager = ctx.obj["services"]

    output = get_output_handler(ctx)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    allowed_domains = bench.bench_config.get_all_domains()
    if domain not in allowed_domains:
        output.display_error(
            f"Domain '{domain}' is not configured for bench '{benchname}'.\n"
            f"Allowed domains: {', '.join(allowed_domains)}\n"
            f"To add an alias domain, use: fm update {benchname} --add-alias {domain}",
        )
        raise typer.Exit(1)

    if cname and challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        output.display_error("CNAME delegation (--cname) can only be used with DNS-01 challenge")
        raise typer.Exit(1)

    if dns_provider and challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        output.display_error("A DNS credential label (--dns-provider) can only be used with DNS-01 challenge")
        raise typer.Exit(1)

    output.change_head(f"Adding SSL certificate for {domain}")

    if dev:
        if cname:
            output.display_error("--cname is not applicable to dev certificates")
            raise typer.Exit(1)
        if dns_provider:
            output.display_error("--dns-provider is not applicable to dev certificates")
            raise typer.Exit(1)
        cert = SSLCertificate(
            domain=domain,
            ssl_type=SUPPORTED_SSL_TYPES.dev,
        )
    else:
        cert = build_letsencrypt_certificate(domain, challenge, cname, dns_provider=dns_provider)
        if dns_provider:
            # Resolve now: at issuance a mistyped label aborts the run after nginx and config work,
            # whereas here the user is still sitting in front of the command.
            try:
                resolve_dns_provider(cert, bench.bench_config)
            except SSLDNSProviderNotConfigured as e:
                output.display_error(str(e))
                raise typer.Exit(1) from None
            output.print(f"Using DNS credentials '{dns_provider}'", emoji_code=":information:")
        if cname:
            output.print(f"Using CNAME delegation: {cname}", emoji_code=":information:")

    with spinner(output, f"Adding SSL certificate for {domain}"):
        bench.certificate_manager.add_certificate(cert, dry_run=dry_run)

    if not dry_run:
        # Update host_name to HTTPS since SSL is now active
        try:
            bench.set_bench_site_config({"host_name": f"https://{domain}"})
            output.debug(f"Updated host_name to https://{domain}")
        except Exception as e:
            # Non-fatal -- site config may not exist yet if site isn't created
            output.debug(f"Could not update host_name to https://{domain}: {e}")
        output.print(f"SSL certificate added for {domain}", emoji_code=":white_check_mark:")
        output.print("Certificate has been issued and configured.", emoji_code=":zap:")


def _remove_bench_certificate(ctx: typer.Context, benchname: str, domain: str, yes: bool):
    services_manager = ctx.obj["services"]

    output = get_output_handler(ctx)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    domains = bench.bench_config.get_all_domains()
    if domain not in domains:
        output.display_error(f"Domain '{domain}' is not configured for bench '{benchname}'")
        raise typer.Exit(1)

    output.change_head(f"Removing SSL certificate for {domain}")

    if not yes:
        choice = output.prompt_ask(
            prompt=f"Remove SSL certificate for {domain}?",
            choices=["yes", "no"],
            default="no",
            required_flag="--yes or -y",
        )
        if choice != "yes":
            output.print("Cancelled.", emoji_code=":x:")
            raise typer.Exit(0)

    output.change_head(f"Removing SSL certificate for {domain}")

    try:
        with spinner(output, f"Removing SSL certificate for {domain}"):
            bench.certificate_manager.remove_certificate_by_domain(domain)

        # Revert host_name to HTTP since SSL was removed
        try:
            bench.set_bench_site_config({"host_name": f"http://{domain}"})
            output.debug(f"Updated host_name to http://{domain}")
        except Exception as e:
            output.debug(f"Could not update host_name to http://{domain}: {e}")

        output.print(f"SSL certificate removed for {domain}", emoji_code=":white_check_mark:")

    except SSLCertificateNotFoundError as e:
        output.display_error(f"Certificate not found: {e}")
        raise typer.Exit(1) from None
    except Exception as e:
        output.display_error(f"Failed to remove certificate: {e}")
        output.display_error(f"Error details: {e!s}")
        raise typer.Exit(1) from None


def _dns_provider_cell(bench_config: "BenchConfig", cert: SSLCertificate | None) -> str:
    """The credential set a DNS-01 certificate will authenticate with, for display only."""
    if cert is None or cert.challenge_type != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        return "N/A"

    label = getattr(cert, "dns_provider", None)

    try:
        resolved = resolve_dns_provider(cert, bench_config)
    except Exception:
        # A label pointing at a credential set nobody stored has to read as broken in its own row.
        # Letting the resolver's refusal out would abort the listing of every other domain with it.
        resolved = None

    if resolved is None:
        return f"[fm.error]{label} (missing)[/fm.error]" if label else "[fm.error]none (missing)[/fm.error]"

    return label or "default"


def _list_bench_certificates(ctx: typer.Context, benchname: str):
    """List all SSL certificates for a bench (existing logic extracted)."""

    services_manager = ctx.obj["services"]

    output = get_output_handler(ctx)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    all_domains = bench.bench_config.get_all_domains()

    certs = bench.certificate_manager.list_certificates()

    cert_map = {cert["domain"]: cert for cert in certs}
    # The status dicts carry no label, so the models are needed too; both come from this bench config.
    cert_models = {cert.domain: cert for cert in bench.bench_config.ssl_certificates}

    table = Table(show_header=True, header_style="fm.accent")
    table.add_column("Domain", style="fm.info")
    table.add_column("Type", style="fm.warn")
    table.add_column("Challenge", style="fm.info")
    table.add_column("DNS Provider", style="fm.info")
    table.add_column("Status", style="fm.ok")
    table.add_column("Expiry", style="fm.info")
    table.add_column("Days Left", justify="right")
    table.add_column("Renewal", style="fm.error")

    # Show all domains, whether they have certificates or not
    for domain in all_domains:
        dns_provider = _dns_provider_cell(bench.bench_config, cert_models.get(domain))

        if domain in cert_map:
            # Domain has a certificate configured
            cert = cert_map[domain]
            ssl_type = cert["ssl_type"]
            challenge_type = cert.get("challenge_type") or "N/A"
            status = "✅ Issued" if cert["exists"] else "❌ Not Issued"

            if cert["exists"] and cert["expiry_date"]:
                expiry = cert["expiry_date"].strftime("%Y-%m-%d %H:%M")
                days_left = str(cert["days_until_expiry"])
                renewal = "⚠️ DUE" if cert["needs_renewal"] else "✓ OK"
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

        table.add_row(domain, ssl_type, challenge_type, dns_provider, status, expiry, days_left, renewal)

    output.print_data(table)
