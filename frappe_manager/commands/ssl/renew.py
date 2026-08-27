"""Renew SSL certificates command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.commands.arguments import StandaloneBenchNameArgument
from frappe_manager.output_manager import spinner, temporary_stop
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.exceptions import BenchSSLCertificateNotIssued
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotDueForRenewalError
from frappe_manager.utils.callbacks import prompt_for_bench_selection

from .external_helpers import _renew_all_external_certificates, _renew_external_certificate
from .helpers import get_output_handler


@example(
    "Renew every certificate on a bench",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Renew one domain",
    "{benchname} example.com",
    benchname="mybench",
)
@example(
    "Renew every bench",
    "--all",
)
@example(
    "Renew an external domain",
    "--standalone example.com",
)
@example(
    "Renew one that is not due yet",
    "{benchname} example.com --force",
    benchname="mybench",
)
def renew(
    ctx: typer.Context,
    benchname: StandaloneBenchNameArgument = None,
    domain: Annotated[
        str | None,
        typer.Argument(help="Domain to renew. Omit to renew every certificate in scope."),
    ] = None,
    all: Annotated[bool, typer.Option(help="Renew every bench, or with --standalone every external domain.")] = False,
    standalone: Annotated[bool, typer.Option("--standalone", help="Renew an external (non-bench) domain.")] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Rehearse against Let's Encrypt staging. Nothing on disk changes."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Renew even when the certificate is not near expiry yet."),
    ] = False,
):
    """
    Renew SSL certificates before they expire.

    Renews one bench by default, or a single domain of it if you name one. --all covers every bench, and --standalone switches to the external Docker project domains.

    A certificate that is not yet due is reported and left alone, unless you pass --force.
    """

    if standalone:
        if all:
            _renew_all_external_certificates(ctx, dry_run, force)
        else:
            actual_domain = domain if domain else benchname
            if not actual_domain:
                output = get_output_handler(ctx)
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
                output = get_output_handler(ctx)
                output.display_error("Benchname required in bench mode")
                with temporary_stop(output):
                    typer.echo(ctx.get_help())
                raise typer.Exit(1)
            sites_list = [benchname]

        for benchname in sites_list:
            output = get_output_handler(ctx)
            bench = Bench.get_object(benchname, services_manager, output_handler=output)

            output.change_head(f"Renew certificate for {benchname}")
            try:
                if domain:
                    cert_domains = [cert.domain for cert in bench.certificate_manager.certificates]
                    if domain not in cert_domains:
                        output.display_error(
                            f"No SSL certificate found for domain '{domain}'.\n"
                            f"Configured certificates: {', '.join(cert_domains) if cert_domains else 'None'}\n"
                            f"To add a certificate, use: fm ssl add {benchname} {domain}",
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
                output.display_error(str(e))
                raise typer.Exit(1)
