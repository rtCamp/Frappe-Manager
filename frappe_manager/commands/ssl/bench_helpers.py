"""Helper functions for bench SSL certificate operations."""

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.table import Table

from frappe_manager.output_manager import spinner
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import CustomCertificate, SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateNotFoundError,
    SSLDNSProviderNotConfigured,
)
from frappe_manager.ssl_manager.letsencrypt_certificate import build_letsencrypt_certificate
from frappe_manager.ssl_manager.ssl_utils import resolve_dns_provider
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME

from .helpers import get_output_handler

if TYPE_CHECKING:
    from frappe_manager.site_manager.bench_config import BenchConfig


def _site_serving(bench: Bench, domain: str) -> str | None:
    """The site `domain` is a hostname for, or None when the bench does not map it.

    `host_name` is per-site data: Frappe builds absolute URLs from it, so the site whose
    certificate changed is the only one whose value should move. Writing `bench.site_name` instead
    meant a certificate issued for a SIBLING site rewrote the primary site's `host_name` to the
    sibling's domain and left the sibling untouched, so the primary began generating links, password
    resets and emails pointing at another site.

    None rather than a fallback: writing the wrong site's config is the bug being fixed, and the
    caller treats a missing value as "leave host_name alone".
    """
    return bench.bench_config.get_site_mappings().get(domain)


def _regenerate_bench_compose(bench: Bench, output) -> bool:
    """Resync `docker-compose.yml` (and the workers compose, if one exists) with the bench's
    current config after a certificate add/remove.

    This is a pure file write -- no docker call, nothing disruptive -- and it is the ONLY thing
    that carries a `--dev`/`--custom --ca` certificate's CA trust (the mount plus
    NODE_EXTRA_CA_CERTS/REQUESTS_CA_BUNDLE, see ssl_ca_trust.py) into the compose file at all:
    `fm ssl add`/`remove` used to call neither `generate_compose`, so that trust never landed
    until some UNRELATED later command happened to regenerate compose, and `fm restart` can never
    apply it (`docker compose restart` reuses a container's already-created mounts/env; it does
    not re-read the compose file the way `compose up` does). Idempotent, so calling it once per
    certificate in a batch of adds costs nothing beyond the write itself -- the actual container
    recreation is deliberately left to the operator's own `fm start`, once, at the end of the
    batch, rather than bounced automatically here on every single certificate.

    Returns True if the compose files were rewritten, so the caller only prints the converge
    instruction when there is actually something to converge.
    """
    try:
        bench.generate_compose(bench.bench_config.export_to_compose_inputs())
        if bench.workers.compose_file_manager.compose_path.exists():
            bench.workers.generate_compose()
    except Exception as e:
        # Non-fatal: the certificate itself is already added/removed by the time this runs. A
        # bench left on a stale compose still works; it just needs a manual nudge to catch up.
        output.warning(f"Could not update {bench.name}'s compose files: {e}. Run 'fm update {bench.name}' to retry.")
        return False
    else:
        return True


def _add_bench_certificate(
    ctx: typer.Context,
    benchname: str,
    domain: str,
    challenge: LETSENCRYPT_PREFERRED_CHALLENGE,
    cname: str | None,
    dry_run: bool,
    dev: bool = False,
    dns_provider: str | None = None,
    custom: bool = False,
    cert_path: Path | None = None,
    key_path: Path | None = None,
    ca_path: Path | None = None,
):
    """Add SSL certificate for a bench domain (existing logic extracted)."""

    services_manager = ctx.obj["services"]

    output = get_output_handler(ctx)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    allowed_domains = bench.bench_config.domains
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
    elif custom:
        # cname/dns_provider/challenge/dry_run/standalone incompatibilities are already refused in
        # add.py before this is reached; nothing left to guard here.
        cert = CustomCertificate(
            domain=domain,
            ssl_type=SUPPORTED_SSL_TYPES.custom,
            cert_source=cert_path,
            key_source=key_path,
            ca_source=ca_path,
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
        # The site this domain serves, not the bench's own: see _site_serving.
        served = _site_serving(bench, domain)
        try:
            if served:
                bench.set_bench_site_config(served, {"host_name": f"https://{domain}"})
                output.debug(f"Updated host_name to https://{domain} on {served}")
            else:
                output.debug(f"No site maps {domain}; leaving host_name alone")
        except Exception as e:
            # Non-fatal -- site config may not exist yet if site isn't created
            output.debug(f"Could not update host_name to https://{domain}: {e}")
        output.print(f"SSL certificate added for {domain}", emoji_code=":white_check_mark:")
        output.print("Certificate has been issued and configured.", emoji_code=":zap:")

        if _regenerate_bench_compose(bench, output):
            output.print(
                f"Run 'fm start {benchname}' to apply it (recreates only the services whose "
                "definition changed; running jobs are undisturbed until then).",
                emoji_code=":information:",
            )


def _remove_bench_certificate(ctx: typer.Context, benchname: str, domain: str, yes: bool):
    services_manager = ctx.obj["services"]

    output = get_output_handler(ctx)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    domains = bench.bench_config.domains
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

        # The site this domain serves, not the bench's own: see _site_serving.
        served = _site_serving(bench, domain)
        try:
            if served:
                bench.set_bench_site_config(served, {"host_name": f"http://{domain}"})
                output.debug(f"Updated host_name to http://{domain} on {served}")
            else:
                output.debug(f"No site maps {domain}; leaving host_name alone")
        except Exception as e:
            output.debug(f"Could not update host_name to http://{domain}: {e}")

        output.print(f"SSL certificate removed for {domain}", emoji_code=":white_check_mark:")

        if _regenerate_bench_compose(bench, output):
            output.print(
                f"Run 'fm start {benchname}' to apply it (recreates only the services whose "
                "definition changed; running jobs are undisturbed until then).",
                emoji_code=":information:",
            )

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

    all_domains = bench.bench_config.domains

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
                if ssl_type == "custom":
                    # fm never auto-renews this type (no ACME account, no stored source bytes),
                    # so "DUE"/"OK" -- which imply `fm ssl renew` acts on this row -- would be a
                    # promise the command does not keep. Name the real action instead.
                    renewal = "⚠️ re-import" if cert["needs_renewal"] else "manual"
                else:
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


def _resolve_domains(ctx: typer.Context, benchname: str, domain: str) -> list[str]:
    """The domains one `BENCH/DOMAIN` address selects.

    `BENCH/all` is every hostname the bench serves, which is the only form that can say "issue for
    everything" without naming each one; anything else is that single domain, returned as given so
    the caller's own check still reports an unknown one with the allowed list.
    """
    if domain != RESERVED_BENCH_NAME:
        return [domain]

    services_manager = ctx.obj["services"]
    output = get_output_handler(ctx)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
    return list(bench.bench_config.domains)


def _prompt_for_domain(ctx: typer.Context, benchname: str, domain: str | None) -> str | None:
    """The domain half of a `BENCH/DOMAIN` address, picked from what the bench actually serves.

    `add` and `remove` are the only two `ssl` subcommands where omitting the second segment has no
    meaning: `list` and `renew` cover every certificate the bench holds, but a certificate can only
    be issued or deleted for one named hostname. They used to run the bench picker and then refuse
    the answer it produced, so the pick list is `bench_config.domains` plus `all`: exactly what
    :func:`_resolve_domains` expands and what the callers verify a domain against, which is why
    picking here cannot produce a value the command then rejects.

    The rows are whole addresses, `shop/b.example.com` rather than `b.example.com`, because the
    argument's grammar is `BENCH/DOMAIN` and a menu of bare hostnames is the one place an operator
    reads the parts without ever seeing the form they compose into. Only the domain half is
    returned: the callers already hold the bench and check the domain against its own list.

    Returns None when there is nothing to offer or no terminal to offer it on, leaving the caller's
    own error to say what the address should have looked like.
    """
    if domain:
        return domain

    output = get_output_handler(ctx)
    try:
        bench = Bench.get_object(benchname, ctx.obj["services"], output_handler=output)
        domains = sorted(bench.bench_config.domains)
    except Exception:
        # An unreadable or half-built bench has no list to pick from; the caller reports the address.
        return None

    if not domains:
        return None

    # NOT short-circuited when the bench serves one domain. Issuing and deleting a certificate are
    # a rate limit and a blast radius, which is why `add` and `remove` refuse a bare `all` where the
    # other subcommands take it: answering an incomplete address on the operator's behalf is the
    # same inference wearing a smaller number. A one-option prompt is a confirmation, not friction.

    try:
        selected = output.prompt_fuzzy(
            prompt="Select address (↑↓ navigate, type to search)",
            choices=[f"{benchname}/{part}" for part in (*domains, RESERVED_BENCH_NAME)],
            vi_mode=True,
            mandatory=True,
            qmark="🤔",
            amark="🤔",
        )
    except Exception:
        return None

    # A domain never contains `/` and neither does a bench name, so the first one is the separator.
    return selected.split("/", 1)[1] if selected else None
