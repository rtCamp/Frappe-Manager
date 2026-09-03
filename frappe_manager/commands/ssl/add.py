"""Add SSL certificate command."""

from pathlib import Path
from typing import Annotated

import typer
from click.core import ParameterSource
from typer_examples import example

from frappe_manager.commands.arguments import BenchDomainArgument
from frappe_manager.output_manager import temporary_stop
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME, prompt_for_bench_selection

from .bench_helpers import _add_bench_certificate, _prompt_for_domain, _resolve_domains
from .external_helpers import _add_external_certificate
from .helpers import get_output_handler


@example(
    "Issue a certificate for a bench domain",
    "{benchname}/example.com",
    benchname="mybench",
)
@example(
    "Issue one when the domain has no public A record",
    "{benchname}/example.com --challenge dns01",
    detail="DNS-01 needs provider credentials, see fm ssl dns-config.",
    benchname="mybench",
)
@example(
    "Rehearse against the staging server first",
    "{benchname}/example.com --dry-run",
    benchname="mybench",
)
@example(
    "Issue for every domain the bench serves",
    "{benchname}/all",
    detail="One certificate per hostname, each site's own name and its aliases. Bare 'all' is refused here: issuing across every bench at once can cross Let's Encrypt's rate limit.",
    benchname="mybench",
)
@example(
    "Issue for an external Docker project",
    "example.com --standalone",
)
@example(
    "Import an operator-issued certificate",
    "{benchname}/example.com --custom --cert ./example.com.crt --key ./example.com.key",
    detail="No issuance: fm copies the files in, links them into the global proxy, and restarts it. Add --ca to also trust a private CA for outbound self-calls once you run 'fm start BENCH' to apply the updated compose.",
    benchname="mybench",
)
@example(
    "Validate through a delegated zone",
    "{benchname}/example.com --challenge dns01 --cname acme.example.net",
    detail="acme.sh looks for _acme-challenge.acme.example.net instead of the bench's own zone.",
    benchname="mybench",
)
@example(
    "Authenticate DNS-01 against a second Cloudflare account",
    "{benchname}/example.com --challenge dns01 --dns-provider acct-b",
    detail="acct-b is a label stored by fm ssl dns-config cloudflare --name acct-b, at either global or bench scope.",
    benchname="mybench",
)
def add_certificate(
    ctx: typer.Context,
    address: BenchDomainArgument = None,
    challenge: Annotated[
        LETSENCRYPT_PREFERRED_CHALLENGE,
        typer.Option("--challenge", "-c", help="ACME validation method."),
    ] = LETSENCRYPT_PREFERRED_CHALLENGE.http01,
    cname: Annotated[
        str | None,
        typer.Option("--cname", help="Delegated zone for _acme-challenge. dns01 only."),
    ] = None,
    dns_provider: Annotated[
        str | None,
        typer.Option(
            "--dns-provider",
            help="Label of the \\[ssl.dns_providers] credential set that authenticates this domain, from fm ssl dns-config cloudflare --name. Omit for the default account. dns01 only.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Rehearse against Let's Encrypt staging. Nothing is kept: no certificate, no nginx change.",
        ),
    ] = False,
    standalone: Annotated[
        bool,
        typer.Option(
            "--standalone",
            help="For an external Docker project on the fm-global-frontend-network network.",
        ),
    ] = False,
    dev: Annotated[
        bool,
        typer.Option(
            "--dev",
            help="Issue from fm's local CA, so no internet or public DNS is needed. Bench mode only.",
        ),
    ] = False,
    skip_dns_check: Annotated[
        bool,
        typer.Option(
            "--skip-dns-check",
            help="Skip the DNS pre-check. Standalone mode only.",
        ),
    ] = False,
    wait_for_dns: Annotated[
        bool,
        typer.Option(
            "--wait-for-dns",
            help="Wait up to 5 min for the CNAME. Standalone only.",
        ),
    ] = False,
    custom: Annotated[
        bool,
        typer.Option(
            "--custom",
            help="Import an operator-supplied certificate instead of issuing one. Needs --cert and --key. Bench mode only.",
        ),
    ] = False,
    cert: Annotated[
        Path | None,
        typer.Option("--cert", help="Certificate file (PEM). --custom only."),
    ] = None,
    key: Annotated[
        Path | None,
        typer.Option("--key", help="Private key file (PEM), unencrypted. --custom only."),
    ] = None,
    ca: Annotated[
        Path | None,
        typer.Option(
            "--ca",
            help="CA bundle file (PEM). Optional; when given, bench containers trust it for outbound self-calls once you run 'fm start BENCH' to apply the updated compose. --custom only.",
        ),
    ] = None,
):
    """
    Issue or import an SSL certificate for a domain and point nginx at it.

    Bench mode takes a bench name and one of its configured domains (add new ones with fm update --add-alias). Naming just the bench offers its domains to pick from. --standalone issues for an external Docker project instead.
    """

    if dev and standalone:
        output = get_output_handler(ctx)
        output.display_error("--dev cannot be used with --standalone mode")
        raise typer.Exit(1)

    # An external domain's config has no field to record the label, so a binding made here would be
    # dropped on the next read and the certificate would renew against the default account instead.
    if dns_provider and standalone:
        output = get_output_handler(ctx)
        output.display_error("--dns-provider is bench mode only; external domains use the default DNS credentials")
        raise typer.Exit(1)

    # Both of these steer the standalone branch's DNS pre-check. The bench branch has no such
    # pre-check to skip or poll, and _add_bench_certificate takes neither, so accepting them here
    # would drop them without a word.
    if skip_dns_check and not standalone:
        output = get_output_handler(ctx)
        output.display_error(
            "--skip-dns-check is --standalone only; a bench certificate is issued without a DNS pre-check, "
            "so there is nothing to skip"
        )
        raise typer.Exit(1)

    if wait_for_dns and not standalone:
        output = get_output_handler(ctx)
        output.display_error(
            "--wait-for-dns is --standalone only; a bench certificate is issued without waiting on DNS propagation"
        )
        raise typer.Exit(1)

    if custom and dev:
        output = get_output_handler(ctx)
        output.display_error("--custom cannot be used with --dev")
        raise typer.Exit(1)

    if custom and standalone:
        output = get_output_handler(ctx)
        output.display_error("--custom is bench mode only; --standalone is not supported yet")
        raise typer.Exit(1)

    if custom and dry_run:
        output = get_output_handler(ctx)
        output.display_error(
            "--custom cannot be used with --dry-run: there is no staging server to rehearse an import against"
        )
        raise typer.Exit(1)

    if custom and cname:
        output = get_output_handler(ctx)
        output.display_error("--cname is not applicable to --custom (there is no ACME challenge to delegate)")
        raise typer.Exit(1)

    if custom and dns_provider:
        output = get_output_handler(ctx)
        output.display_error("--dns-provider is not applicable to --custom (there is no ACME challenge to authenticate)")
        raise typer.Exit(1)

    # --challenge always defaults to http01 (LETSENCRYPT_PREFERRED_CHALLENGE.http01), so `challenge
    # is None` can never distinguish "the user asked for it" from "nothing was passed" -- only the
    # parameter source can. Mirrors restart.py's drain_explicit check.
    if custom and ctx.get_parameter_source("challenge") == ParameterSource.COMMANDLINE:
        output = get_output_handler(ctx)
        output.display_error("--challenge is not applicable to --custom: there is no ACME challenge to perform")
        raise typer.Exit(1)

    if not custom and (cert or key or ca):
        output = get_output_handler(ctx)
        output.display_error("--cert/--key/--ca require --custom")
        raise typer.Exit(1)

    if custom and (not cert or not key):
        output = get_output_handler(ctx)
        output.display_error("--custom requires both --cert and --key")
        raise typer.Exit(1)

    for flag_name, path in (("--cert", cert), ("--key", key), ("--ca", ca)):
        if path and not path.exists():
            output = get_output_handler(ctx)
            output.display_error(f"{flag_name} file not found: {path}")
            raise typer.Exit(1)

    # The address's second segment, put there by `bench_domain_callback`. In standalone mode there
    # is no bench, so the external domain arrives as the whole (unslashed) argument instead.
    domain = ctx.obj.get("domain") if ctx.obj else None

    if address == RESERVED_BENCH_NAME:
        output = get_output_handler(ctx)
        output.display_error(
            "'all' is not accepted here: issuing a certificate for every domain of every bench can "
            "cross Let's Encrypt's rate limit in one command. Name a bench, and use 'BENCH/all' for "
            "every domain of that one."
        )
        raise typer.Exit(1)

    if standalone:
        if domain:
            output = get_output_handler(ctx)
            output.display_error(
                "An external domain belongs to no bench, so --standalone takes a bare domain: "
                f"use 'fm ssl add {domain} --standalone'."
            )
            raise typer.Exit(1)

        if not address:
            output = get_output_handler(ctx)
            output.display_error("Domain is required in standalone mode")
            with temporary_stop(output):
                typer.echo(ctx.get_help())
            raise typer.Exit(1)

        _add_external_certificate(ctx, address, challenge, cname, dry_run, skip_dns_check, wait_for_dns)
        return

    address = prompt_for_bench_selection(address)

    if address:
        domain = _prompt_for_domain(ctx, address, domain)

    if not address or not domain:
        output = get_output_handler(ctx)
        output.display_error(
            "An address of the form BENCH/DOMAIN is required in bench mode, naming the hostname the "
            "certificate is for. 'BENCH/all' issues one for every domain the bench serves."
        )
        with temporary_stop(output):
            typer.echo(ctx.get_help())
        raise typer.Exit(1)

    for target in _resolve_domains(ctx, address, domain):
        _add_bench_certificate(
            ctx,
            address,
            target,
            challenge,
            cname,
            dry_run,
            dev=dev,
            dns_provider=dns_provider,
            custom=custom,
            cert_path=cert,
            key_path=key,
            ca_path=ca,
        )
