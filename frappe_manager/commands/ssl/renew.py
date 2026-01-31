"""Renew SSL certificates command."""

from typing import Annotated, Optional
import typer
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.site import Bench
from frappe_manager.site_manager.exceptions import BenchSSLCertificateNotIssued
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotDueForRenewalError
from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import temporary_stop, spinner
from frappe_manager.utils.callbacks import sites_autocompletion_callback, prompt_for_bench_selection
from .helpers import get_output_handler
from .external_helpers import _renew_external_certificate, _renew_all_external_certificates

ssl_renew_command = typer.Typer(no_args_is_help=True)


@ssl_renew_command.command()
def renew(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench (omit for standalone mode).", autocompletion=sites_autocompletion_callback
        ),
    ] = None,
    domain: Annotated[
        Optional[str],
        typer.Argument(help="Specific domain to renew. If omitted, renews all certificates for the bench/standalone."),
    ] = None,
    all: Annotated[bool, typer.Option(help="Renew ssl cert for all benches.")] = False,
    standalone: Annotated[bool, typer.Option("--standalone", help="Renew certificates for external domains")] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Test renewal using Let's Encrypt staging server without modifying the system."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force renewal even if certificate is not due for renewal."),
    ] = False,
):
    """
    Renew SSL certificates.

    Supports both bench mode (default) and standalone mode for external domains.
    Use --dry-run to test with Let's Encrypt staging server.
    Use --force to renew certificates regardless of expiry date.
    """

    if standalone:
        if all:
            _renew_all_external_certificates(ctx, dry_run, force)
        else:
            actual_domain = domain if domain else benchname
            if not actual_domain:
                context = LoggerContext(operation="ssl-renew-external")
                output = get_output_handler(ctx, context=context)
                output.display_error("Domain required for standalone renewal")
                with temporary_stop(output):
                    typer.echo(ctx.get_help())
                raise typer.Exit(1)
            _renew_external_certificate(ctx, actual_domain, dry_run, force)
    else:
        # Existing bench renewal logic
        services_manager = ctx.obj["services"]
        bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager)

        if all:
            sites_list = bench_service.get_bench_names()
        else:
            benchname = prompt_for_bench_selection(benchname)

            if not benchname:
                context = LoggerContext(operation="ssl-renew")
                output = get_output_handler(ctx, context=context)
                output.display_error("Benchname required in bench mode")
                with temporary_stop(output):
                    typer.echo(ctx.get_help())
                raise typer.Exit(1)
            sites_list = [benchname]

        for benchname in sites_list:
            context = LoggerContext(bench=benchname, operation="ssl-renew")
            output = get_output_handler(ctx, context=context)
            bench = Bench.get_object(benchname, services_manager, output_handler=output)

            output.change_head(f"Renew certificate for {benchname}")
            try:
                if domain:
                    cert_domains = [cert.domain for cert in bench.certificate_manager.certificates]
                    if domain not in cert_domains:
                        output.display_error(
                            f"No SSL certificate found for domain '{domain}'.\n"
                            f"Configured certificates: {', '.join(cert_domains) if cert_domains else 'None'}\n"
                            f"To add a certificate, use: fm ssl add {benchname} {domain}"
                        )
                        raise typer.Exit(1)

                    # Renew specific domain certificate
                    with spinner(output, f"Renewing certificate for {domain}"):
                        bench.ssl.renew_certificate(domain, dry_run=dry_run, force=force)
                    if not dry_run:
                        output.print(f"Certificate renewed for {domain}", emoji_code=":white_check_mark:")
                else:
                    # Renew all certificates for the bench
                    with spinner(output, f"Renewing certificates for {benchname}"):
                        bench.ssl.renew_all_certificates(dry_run=dry_run, force=force)
            except (BenchSSLCertificateNotIssued, SSLCertificateNotDueForRenewalError) as e:
                output.warning(e.message)

            except Exception as e:
                output.warning(str(e))
