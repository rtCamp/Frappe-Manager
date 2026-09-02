"""Renew SSL certificates command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import BenchDomainAllArgument
from frappe_manager.output_manager import spinner, temporary_stop
from frappe_manager.site_manager.exceptions import BenchSSLCertificateNotIssued
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotDueForRenewalError
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME, prompt_for_bench_selection, resolve_bench_targets

from .external_helpers import _renew_all_external_certificates, _renew_external_certificate
from .helpers import get_output_handler


@example(
    "Renew every certificate on a bench",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Renew one domain",
    "{benchname}/example.com",
    benchname="mybench",
)
@example(
    "Renew every certificate on one bench, named explicitly",
    "{benchname}/all",
    benchname="mybench",
)
@example(
    "Renew every bench",
    "all",
    detail="'all' goes where a bench name goes. One bench failing is reported and the rest still renew.",
)
@example(
    "Renew an external domain",
    "example.com --standalone",
    detail="An external domain belongs to no bench, so it is named bare rather than as an address.",
)
@example(
    "Renew one that is not due yet",
    "{benchname}/example.com --force",
    benchname="mybench",
)
def renew(
    ctx: typer.Context,
    benchname: BenchDomainAllArgument = None,
    standalone: Annotated[bool, typer.Option("--standalone", help="Renew an external (non-bench) domain.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Rehearse against Let's Encrypt staging.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Renew even when the certificate is not due.")] = False,
):
    """
    Renew SSL certificates before they expire.

    Renews every certificate of one bench by default, or a single one when the address names a domain. 'all' covers every bench, and --standalone switches to the external Docker project domains.

    A certificate that is not yet due is reported and left alone, unless you pass --force.
    """

    # The address's second segment, put there by `bench_domain_callback`.
    domain = ctx.obj.get("domain") if ctx.obj else None

    if standalone:
        if domain:
            output = get_output_handler(ctx)
            output.display_error(
                "An external domain belongs to no bench, so --standalone takes a bare domain: "
                f"use 'fm ssl renew {domain} --standalone'."
            )
            raise typer.Exit(1)
        if benchname == RESERVED_BENCH_NAME:
            _renew_all_external_certificates(ctx, dry_run, force)
            return
        if not benchname:
            output = get_output_handler(ctx)
            output.display_error("Domain required for standalone renewal")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)
        _renew_external_certificate(ctx, benchname, dry_run, force)
        return

    services_manager = ctx.obj["services"]

    if benchname != RESERVED_BENCH_NAME:
        benchname = prompt_for_bench_selection(benchname)
        if not benchname:
            output = get_output_handler(ctx)
            output.display_error("Benchname required in bench mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

    targets = resolve_bench_targets(benchname)
    output = get_output_handler(ctx)
    failed: list[str] = []

    for benchname in targets:
        # Inside the loop, and inside the try below, because a bench whose config will not load used
        # to escape as itself and take every remaining bench with it: on a command run from cron,
        # one broken bench silently meant nothing after it was renewed.
        try:
            bench = Bench.get_object(benchname, services_manager, output_handler=output)
        except Exception as e:
            output.display_error(f"{benchname}: {e}")
            failed.append(benchname)
            continue

        output.change_head(f"Renew certificate for {benchname}")
        try:
            if domain and domain != RESERVED_BENCH_NAME:
                cert_domains = [cert.domain for cert in bench.certificate_manager.certificates]
                if domain not in cert_domains:
                    output.display_error(
                        f"No SSL certificate found for domain '{domain}'.\n"
                        f"Configured certificates: {', '.join(cert_domains) if cert_domains else 'None'}\n"
                        f"To add a certificate, use: fm ssl add {benchname}/{domain}",
                    )
                    raise typer.Exit(1)

                with spinner(output, f"Renewing certificate for {domain}"):
                    bench.ssl.renew_certificate(domain, dry_run=dry_run, force=force)
                if not dry_run:
                    output.print(f"Certificate renewed for {domain}", emoji_code=":white_check_mark:")
            else:
                # No domain, or `BENCH/all`: every certificate the bench holds. The two mean the
                # same thing, because a bench's certificates ARE its domains' certificates.
                with spinner(output, f"Renewing certificates for {benchname}"):
                    bench.ssl.renew_all_certificates(dry_run=dry_run, force=force)
        except (BenchSSLCertificateNotIssued, SSLCertificateNotDueForRenewalError) as e:
            output.warning(e.message)
        except typer.Exit:
            raise
        except Exception as e:
            # Report and carry on. Aborting here meant one bench's ACME failure left every bench
            # after it unrenewed, which on a scheduled run is discovered when a certificate expires.
            output.display_error(f"{benchname}: {e}")
            failed.append(benchname)

    if failed:
        output.display_error(f"Renewal failed for: {', '.join(failed)}")
        raise typer.Exit(1)
