from pathlib import Path
from typing import TYPE_CHECKING

import typer

from frappe_manager import CLI_SERVICES_DIRECTORY
from frappe_manager.output_manager import OutputHandler, railcard, spinner
from frappe_manager.site_manager.modules.cdn_detection import CDNProxyStatus, detect_cloudflare_proxy
from frappe_manager.site_manager.modules.public_scheme import host_proxy_state, public_scheme
from frappe_manager.site_manager.modules.realip import trusted_ranges
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
from frappe_manager.utils.config_keys import declared_field
from frappe_manager.utils.site import resolve_known_name

from .external_helpers import proxy_backend_domains
from .helpers import cert_expiry_words, cert_status_word, get_output_handler

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


def _print_cdn_hint(output: OutputHandler, domain: str) -> None:
    """Advisory only: never raises, never changes what `fm ssl add` does. `detect_cloudflare_proxy`
    itself guarantees no exception reaches here; `undetermined` (no A record, DNS timeout, no `dig`,
    ...) prints nothing, because there is nothing established to report.
    """
    result = detect_cloudflare_proxy(domain, output=output)
    trusted = bool(trusted_ranges(CLI_SERVICES_DIRECTORY / "nginx-proxy" / "confd"))
    if result.status == CDNProxyStatus.proxied and not trusted:
        output.print(
            f"{domain} resolves into Cloudflare's published ranges. If it is proxied (orange-clouded), "
            "run 'fm services trusted-proxies set --cdn cloudflare' so the redirect and self-calls "
            "stop assuming a direct TLS connection.",
            emoji_code=":information:",
        )
    elif result.status == CDNProxyStatus.not_proxied and trusted:
        output.print(
            f"{domain} does not currently resolve into a known CDN range; the host's trusted-proxies "
            "set may not be necessary here.",
            emoji_code=":information:",
        )


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


def _replace_existing_custom_certificate(output, bench, domain: str, custom: bool, yes: bool) -> None:
    """Let a `--custom` add rotate the certificate it already holds for this domain.

    `add_certificate` refuses any domain it already knows, which is right for issuance -- a second
    Let's Encrypt add would mint a duplicate and burn a rate limit. It is wrong for an import: fm
    stores only the BYTES of a custom certificate, never the --cert/--key paths, so re-running the
    add is the only way to rotate one, and it is exactly what `SSLCertificateManualRenewalRequired`
    tells the operator to do. Refusing it made that instruction a dead end whose only workaround,
    remove-then-add, takes HTTPS down in between.

    Replacement is expressed on the add verb rather than a `renew --custom`, following certbot's
    `--cert-name` and ACM's `import-certificate --certificate-arn`: renew means the ISSUER produces
    new bytes, which is the one thing an imported certificate has no way to do.

    Changing TYPE is still refused. ACM's reimport has the same rule -- material may be refreshed,
    identity may not -- and a `--custom` add landing on a Let's Encrypt domain is far more likely to
    be a mistyped domain than an intended conversion.
    """
    existing = next((c for c in bench.certificate_manager.certificates if c.domain == domain), None)
    if not existing:
        return

    if not custom or existing.ssl_type != SUPPORTED_SSL_TYPES.custom:
        output.display_error(
            f"'{domain}' already has a {existing.ssl_type.value} certificate. Only a custom "
            f"certificate can be replaced in place; changing type means "
            f"'fm ssl remove {bench.name}/{domain}' first, which serves the domain over plain HTTP "
            f"until the new certificate is added."
        )
        raise typer.Exit(1)

    try:
        current_expiry = bench.certificate_manager.get_certificate_expiry(domain).strftime("%Y-%m-%d")
    except Exception:
        current_expiry = "unknown"

    if not yes:
        choice = output.prompt_ask(
            prompt=f"Replace the custom certificate for {domain} (expires {current_expiry})?",
            choices=["yes", "no"],
            default="no",
            required_flag="--yes or -y",
        )
        if choice != "yes":
            output.print("Aborted; the existing certificate is untouched.", emoji_code=":information:")
            raise typer.Exit(1)

    bench.certificate_manager.remove_certificate_by_domain(domain)


def _add_bench_certificate(
    ctx: typer.Context,
    benchname: str,
    domain: str,
    challenge: LETSENCRYPT_PREFERRED_CHALLENGE,
    cname: str | None,
    test_ca: bool,
    dev: bool = False,
    dns_provider: str | None = None,
    custom: bool = False,
    cert_path: Path | None = None,
    key_path: Path | None = None,
    ca_path: Path | None = None,
    yes: bool = False,
):
    """Add SSL certificate for a bench domain (existing logic extracted)."""

    services_manager = ctx.obj["services"]

    output = get_output_handler(ctx)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    allowed_domains = bench.bench_config.domains
    # The one address rule, applied where the served set is known. It cannot live in the parameter
    # callback: `--standalone` manages domains belonging to no bench, and appending `.localhost`
    # to someone else's domain would mangle it. Nothing reaches here in that mode.
    resolved = resolve_known_name(domain, allowed_domains)
    if resolved is None:
        output.display_error(
            f"Domain '{domain}' is not configured for bench '{benchname}'.\n"
            f"Allowed domains: {', '.join(allowed_domains)}\n"
            f"To add an alias domain, use: fm domain add {benchname} {domain}",
        )
        raise typer.Exit(1)
    domain = resolved

    if cname and challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        output.display_error("CNAME delegation (--cname) can only be used with DNS-01 challenge")
        raise typer.Exit(1)

    if dns_provider and challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        output.display_error("A DNS credential label (--dns-provider) can only be used with DNS-01 challenge")
        raise typer.Exit(1)

    output.change_head(f"Adding SSL certificate for {domain}")

    _print_cdn_hint(output, domain)

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
        # cname/dns_provider/challenge/test_ca/standalone incompatibilities are already refused in
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

    _replace_existing_custom_certificate(output, bench, domain, custom=custom, yes=yes)

    with spinner(output, f"Adding SSL certificate for {domain}"):
        bench.certificate_manager.add_certificate(cert, test_ca=test_ca)

    if not test_ca:
        # The site this domain serves, not the bench's own: see _site_serving. And only when the
        # domain IS that site's own name: a site's name is its canonical domain
        # (`get_site_mappings` maps `site -> site`, aliases map `alias -> site`), and `host_name`
        # is the canonical URL Frappe builds links, password resets and emails from. Certifying an
        # ALIAS must therefore not rewrite it -- that silently renamed the site to the alias.
        served = _site_serving(bench, domain)
        # No port, even on a host that publishes elsewhere: `host_name` is what Frappe's own
        # server-side calls resolve, and `extra_hosts` sends those straight to the proxy
        # CONTAINER, where only 80/443 exist. A published port is a host-side fact.
        host_name = f"https://{domain}"
        try:
            if served == domain:
                bench.set_bench_site_config(served, {"host_name": host_name})
                output.debug(f"Updated host_name to {host_name} on {served}")
            elif served:
                output.debug(f"{domain} is an alias of {served}; leaving host_name alone")
            else:
                output.debug(f"No site maps {domain}; leaving host_name alone")
        except Exception as e:
            # Non-fatal -- site config may not exist yet if site isn't created
            output.debug(f"Could not update host_name to {host_name}: {e}")
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

    # Certificates the bench HOLDS, not just the domains it serves: `fm domain remove` leaves a
    # certificate behind, and matching only served domains made that leftover unremovable by the
    # one command whose job is removing it.
    held = [cert.domain for cert in bench.certificate_manager.certificates]
    targets = [*bench.bench_config.domains, *(d for d in held if d not in bench.bench_config.domains)]
    resolved = resolve_known_name(domain, targets)
    if resolved is None:
        # Names what the bench DOES serve, the way `fm domain remove` already does. Saying only
        # that the domain is not configured leaves the operator guessing at the spelling.
        output.display_error(
            f"Domain '{domain}' is not configured for bench '{benchname}'. It serves "
            f"{', '.join(repr(d) for d in sorted(bench.bench_config.domains))}."
        )
        raise typer.Exit(1)
    domain = resolved

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
            raise typer.Exit(1)

    output.change_head(f"Removing SSL certificate for {domain}")

    try:
        with spinner(output, f"Removing SSL certificate for {domain}"):
            bench.certificate_manager.remove_certificate_by_domain(domain)

        # Same rule as the add path: only the site's own canonical name moves `host_name` -- an
        # alias's own certificate must not rename the site to the alias and downgrade its
        # canonical URL to http.
        served = _site_serving(bench, domain)
        # Still https when a trusted front terminates TLS for it: fm dropping its own certificate
        # does not make the public connection plaintext, and writing http here would make Frappe
        # email links that the front then redirects, or refuses.
        front, _http_port, _https_port = host_proxy_state()
        host_name = f"{public_scheme(False, front)}://{domain}"
        try:
            if served == domain:
                bench.set_bench_site_config(served, {"host_name": host_name})
                output.debug(f"Updated host_name to {host_name} on {served}")
            elif served:
                output.debug(f"{domain} is an alias of {served}; leaving host_name alone")
            else:
                output.debug(f"No site maps {domain}; leaving host_name alone")
        except Exception as e:
            output.debug(f"Could not update host_name to {host_name}: {e}")

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

def _dns_provider_facts(bench_config: "BenchConfig", cert: SSLCertificate | None) -> tuple[str | None, bool]:
    """(label used, credential missing) for a DNS-01 certificate: the plain-data classification a
    card later renders as its "dns provider" fact."""
    if cert is None or cert.challenge_type != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        return None, False

    label = declared_field(cert, "dns_provider")

    try:
        resolved = resolve_dns_provider(cert, bench_config)
    except Exception:
        # A label pointing at a credential set nobody stored has to read as broken in its own row,
        # rather than aborting every other domain's listing.
        resolved = None

    if resolved is None:
        return label or None, True

    return label or "default", False


def _bench_certificate_rows(bench: Bench, backends: set[str]) -> list[dict]:
    """Structured per-domain certificate facts for `bench`: the data source `--json` reads,
    mirroring `_list_bench_certificates`'s classification without the display formatting
    (icons, "N/A", rich markup) that exists only for a terminal.
    """
    all_domains = bench.bench_config.domains
    certs = bench.certificate_manager.list_certificates()

    cert_map = {cert["domain"]: cert for cert in certs}
    cert_models = {cert.domain: cert for cert in bench.bench_config.ssl_certificates}

    rows: list[dict] = []

    # Certificates whose domain the bench no longer serves, listed AFTER the served ones. Omitting
    # them hid every certificate `fm domain remove` left behind -- material and a private key still
    # on disk, invisible to this command and refused by `fm ssl remove` because the domain is gone
    # from the config. No comparable tool strands a certificate that way: NPM and certbot both keep
    # a cert listable and deletable after whatever used it is gone.
    orphaned = [domain for domain in cert_map if domain not in all_domains]

    for domain in [*all_domains, *sorted(orphaned)]:
        dns_provider, dns_provider_missing = _dns_provider_facts(bench.bench_config, cert_models.get(domain))

        if domain in cert_map:
            cert = cert_map[domain]
            ssl_type = cert["ssl_type"]
            challenge_type = cert.get("challenge_type") or None
            status = "issued" if cert["exists"] else "not_issued"

            if cert["exists"] and cert["expiry_date"]:
                expiry = cert["expiry_date"].isoformat()
                days_left = cert["days_until_expiry"]
                if ssl_type == "custom":
                    renewal = "re_import" if cert["needs_renewal"] else "manual"
                else:
                    renewal = "due" if cert["needs_renewal"] else "ok"
            else:
                expiry = None
                days_left = None
                renewal = None
        else:
            ssl_type = "none"
            challenge_type = None
            status = "none"
            expiry = None
            days_left = None
            renewal = None

        rows.append(
            {
                "domain": domain,
                "certificate_type": ssl_type,
                "challenge_type": challenge_type,
                "dns_provider": dns_provider,
                "dns_provider_missing": dns_provider_missing,
                "status": status,
                "live": domain in backends,
                # A certificate the bench keeps for a domain it no longer serves. nginx stops
                # answering for it immediately, so this is dormant material rather than exposure --
                # but re-adding the domain puts THIS certificate back in service with no issuance
                # step, which is how an expired one comes back as a broken site.
                "orphaned": domain not in all_domains,
                "expiry": expiry,
                "days_until_expiry": days_left,
                "renewal": renewal,
            }
        )

    return rows


def _bench_certificate_data(ctx: typer.Context, benchname: str) -> list[dict]:
    """`_bench_certificate_rows` resolved through `ctx`, the way `_list_bench_certificates` does.
    The single source `fm ssl list BENCH --json` and the `all` selector's structured payload
    both call, so a bench's facts are computed once per call site and never scraped off a card.
    """
    services_manager = ctx.obj["services"]
    output = get_output_handler(ctx)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
    backends = proxy_backend_domains(services_manager)
    return _bench_certificate_rows(bench, backends)


def _bench_cert_meta(row: dict) -> str:
    """Headline meta for one domain's card: status is a WORD first (text carries state; the
    token only enhances it), "live" second -- whether a container is actually publishing
    VIRTUAL_HOST for this hostname is not the same question as whether the certificate is valid:
    a stopped bench, or a domain the bench no longer serves, keeps a perfectly good certificate
    that nothing is using.
    """
    live_token = "fm.ok" if row["live"] else "fm.muted"
    live_word = "live" if row["live"] else "not live"
    return f"{cert_status_word(row['status'])} [fm.muted]·[/fm.muted] [{live_token}]{live_word}[/{live_token}]"


def _dns_provider_fact(row: dict) -> str:
    """Display form of a row's plain `dns_provider`/`dns_provider_missing` facts, built from data
    the row already carries rather than resolving the credential set a second time."""
    if row["dns_provider_missing"]:
        return f"[fm.error]{row['dns_provider'] or 'none'} (missing)[/fm.error]"
    return row["dns_provider"] or "default"


def _bench_certificate_card(row: dict) -> railcard.Card:
    """One domain's certificate as a card: built from the exact `_bench_certificate_rows` row
    `--json` reads, so a card and the structured payload can never disagree.
    """
    card = railcard.Card(row["domain"], _bench_cert_meta(row), active=row["status"] == "issued")

    if row["status"] != "none":
        card.fact("type", row["certificate_type"])
        if row["challenge_type"] == LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
            card.fact("challenge", row["challenge_type"])
            card.fact("dns provider", _dns_provider_fact(row))
        if row.get("orphaned"):
            # Says what it is AND what it does, because "orphaned" alone reads as harmless: the
            # bench stopped serving this domain, so nginx no longer answers for it, but re-adding
            # the domain puts this same certificate back in service without issuing anything.
            card.fact("orphaned", "domain no longer served; still on disk, reused if re-added")
        if row["expiry"] is not None:
            card.fact("expiry", cert_expiry_words(row["expiry"]))
        if row["days_until_expiry"] is not None:
            card.fact("days left", str(row["days_until_expiry"]))
        if row["renewal"] is not None:
            card.fact("renewal", row["renewal"].replace("_", " "))

    return card


def _list_bench_certificates(ctx: typer.Context, benchname: str):
    """List all SSL certificates for a bench: one card per domain, built from the same rows
    `--json` reads (never a second, display-only computation of the same facts).
    """

    services_manager = ctx.obj["services"]

    output = get_output_handler(ctx)

    if output.wants_structured_data:
        output.print_data(_bench_certificate_data(ctx, benchname))
        return

    bench = Bench.get_object(benchname, services_manager, output_handler=output)
    backends = proxy_backend_domains(services_manager)
    rows = _bench_certificate_rows(bench, backends)

    if not any(row["status"] != "none" for row in rows):
        # No domain in the bench has a certificate at all: a card per domain would be a page of
        # identical "no ssl" cards, so this says so once and names how to fix it instead.
        output.print(
            f"No SSL certificates configured for bench '{benchname}'. "
            f"Add one with 'fm ssl add {benchname} <domain>'.",
            emoji_code=":information:",
        )
        return

    output.print_data(railcard.cards([_bench_certificate_card(row) for row in rows]))


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


def _resolve_certificates_to_remove(ctx: typer.Context, benchname: str, domain: str) -> list[str]:
    """The domains `remove` should act on: for `all`, the ones that actually HOLD a certificate.

    `_resolve_domains` answers with every hostname the bench serves, which is right for issuance
    and wrong here: on any real bench the alias domains hold no certificate of their own, so
    removing "everything" aborted at the first of them with `Certificate not found` -- after
    deleting the ones before it and leaving the ones after. `remove --help` already promised "every
    certificate the bench holds", so this is the documented meaning. A domain named EXPLICITLY that
    has no certificate is still an error: that is a typo worth reporting.
    """
    if domain != RESERVED_BENCH_NAME:
        return [domain]

    services_manager = ctx.obj["services"]
    output = get_output_handler(ctx)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
    certified = {cert.domain for cert in bench.certificate_manager.certificates}
    return [domain for domain in bench.bench_config.domains if domain in certified]


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
