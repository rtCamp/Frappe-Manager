"""Helper functions for bench SSL certificate operations."""

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.table import Table

from frappe_manager.output_manager import OutputHandler, spinner
from frappe_manager.site_manager.bench_config import FMBenchEnvType
from frappe_manager.site_manager.modules.cdn_detection import CDNProxyStatus, detect_cloudflare_proxy
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


def _print_cdn_hint(output: OutputHandler, domain: str, behind_proxy: bool) -> None:
    """Advisory only: never raises, never changes what `fm ssl add` does. `detect_cloudflare_proxy`
    itself guarantees no exception reaches here; `undetermined` (no A record, DNS timeout, no `dig`,
    ...) prints nothing, because there is nothing established to report.
    """
    result = detect_cloudflare_proxy(domain, output=output)
    if result.status == CDNProxyStatus.proxied and not behind_proxy:
        output.print(
            f"{domain} resolves into Cloudflare's published ranges. If it is proxied (orange-clouded), "
            "add --behind-proxy so the origin's own redirect and self-calls stop assuming a direct "
            "TLS connection.",
            emoji_code=":information:",
        )
    elif result.status == CDNProxyStatus.not_proxied and behind_proxy:
        output.print(
            f"{domain} does not currently resolve into a known CDN range; --behind-proxy may not be "
            "necessary here.",
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


def _regenerate_bench_supervisor_config(bench: Bench, output) -> bool:
    """Resync `fm-web-server.sh` (the gunicorn wrapper) with the bench's current `--behind-proxy`
    certificates after an add/remove.

    Pure file write, like `_regenerate_bench_compose` above -- but the command that actually
    APPLIES it is the other one. `fm start` only rewrites this file when told with
    `--reconfigure-supervisor`, and even then nothing re-execs the running gunicorn process:
    `docker_ops.start` on an unchanged compose does not recreate the container, so the OLD script
    keeps running underneath. What applies a changed wrapper is `fm restart`: its default
    (non-`--container`) leg runs `supervisorctl restart`, which re-execs the supervisor program's
    own `command=` line -- this exact script -- fresh from disk. This is the mirror image of
    `_regenerate_bench_compose`'s own docstring: that one exists because `fm restart` CANNOT reach
    a container's already-created mounts/env, and this one exists because `fm start` cannot reach
    an already-running supervisor program's in-memory command line.

    Returns True if the file was rewritten, so the caller only prints the restart instruction when
    there is actually something for it to apply.
    """
    try:
        bench.supervisor.setup_supervisor(bench.path, force=True)
    except Exception as e:
        # Non-fatal, same reasoning as _regenerate_bench_compose: the certificate itself is
        # already added/removed by the time this runs.
        output.warning(
            f"Could not update {bench.name}'s gunicorn wrapper: {e}. "
            f"Run 'fm start {bench.name} --reconfigure-supervisor' to retry."
        )
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
    behind_proxy: bool = False,
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

    # --behind-proxy trusts X-Forwarded-Proto for gunicorn (see bench_supervisor.py), and that
    # trust is bench-WIDE: one supervisor-managed gunicorn process serves every site the bench
    # has, unlike the redirect config, which is genuinely per-domain (a separate vhost.d file
    # each). Bench nginx does not recompute the header, it relays whatever it received verbatim
    # (Docker/nginx/template.conf's @webserver location: `proxy_set_header X-Forwarded-Proto
    # $http_x_forwarded_proto`), and the global proxy trusts a client-supplied value unless the
    # request came with none at all (its own `map ... default $http_x_forwarded_proto`). So once
    # ANY domain on a bench turns this on, an anonymous client can forge the header for every
    # OTHER domain that bench serves too -- stripping the Secure flag from a direct domain's
    # session cookie, or spoofing https on a plain request. Scoping the trusted peer IP narrower
    # (just bench nginx) does not close this: bench nginx is the relay, not the origin of the
    # value. A bench must therefore agree on --behind-proxy across every certificate it holds.
    disagreeing = sorted(
        cert.domain for cert in bench.bench_config.ssl_certificates if cert.behind_proxy != behind_proxy
    )
    if disagreeing:
        new_state = "behind an external terminator (--behind-proxy)" if behind_proxy else "not behind one"
        other_state = "not behind one" if behind_proxy else "behind an external terminator (--behind-proxy)"
        verb = "are" if len(disagreeing) > 1 else "is"
        output.display_error(
            f"--behind-proxy trusts the forwarded proto for gunicorn, which serves the whole bench, "
            f"not one domain. {domain} is being added {new_state}, but {', '.join(disagreeing)} on "
            f"'{benchname}' {verb} already configured as {other_state}: gunicorn cannot trust the "
            "header for one domain and not another. Match the bench's existing setting, or use a "
            "separate bench."
        )
        raise typer.Exit(1)

    if cname and challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        output.display_error("CNAME delegation (--cname) can only be used with DNS-01 challenge")
        raise typer.Exit(1)

    if dns_provider and challenge != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        output.display_error("A DNS credential label (--dns-provider) can only be used with DNS-01 challenge")
        raise typer.Exit(1)

    output.change_head(f"Adding SSL certificate for {domain}")

    _print_cdn_hint(output, domain, behind_proxy)

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
            behind_proxy=behind_proxy,
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
            behind_proxy=behind_proxy,
        )
    else:
        cert = build_letsencrypt_certificate(
            domain, challenge, cname, dns_provider=dns_provider, behind_proxy=behind_proxy
        )
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
        # The site this domain serves, not the bench's own: see _site_serving. And only when the
        # domain IS that site's own name: a site's name is its canonical domain
        # (`get_site_mappings` maps `site -> site`, aliases map `alias -> site`), and `host_name`
        # is the canonical URL Frappe builds links, password resets and emails from. Certifying an
        # ALIAS must therefore not rewrite it -- that silently renamed the site to the alias.
        served = _site_serving(bench, domain)
        try:
            if served == domain:
                bench.set_bench_site_config(served, {"host_name": f"https://{domain}"})
                output.debug(f"Updated host_name to https://{domain} on {served}")
            elif served:
                output.debug(f"{domain} is an alias of {served}; leaving host_name alone")
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

        if behind_proxy and _regenerate_bench_supervisor_config(bench, output):
            if bench.bench_config.environment_type == FMBenchEnvType.prod:
                output.print(
                    f"Run 'fm restart {benchname}' to apply the forwarded-proto trust to gunicorn "
                    "(the compose converge above does not reach it).",
                    emoji_code=":information:",
                )
            else:
                # A dev-environment bench runs `bench serve` under supervisor, not gunicorn (see
                # user-script.sh: FRAPPE_ENV=dev links frappe-dev.conf, never web.fm.supervisor.conf,
                # which is the only place fm-web-server.sh is referenced). The wrapper is still
                # written -- harmless, and correct the moment the bench is switched to prod -- but
                # telling the operator to restart something gunicorn never runs here would be an
                # instruction that does nothing. The redirect half of --behind-proxy is unaffected:
                # it runs at the global proxy, independent of which web server this bench runs.
                output.print(
                    f"{domain}'s certificate trusts the forwarded proto for gunicorn, but "
                    f"'{benchname}' is a dev-environment bench (runs 'bench serve', not gunicorn), so "
                    "that trust has nothing to apply to yet. It takes effect if the bench is later "
                    f"switched to prod ('fm update {benchname} --environment prod').",
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

    # Captured before the removal call: `certificate_for` returns a disabled default (behind_proxy
    # False) once the certificate is gone, which would hide that THIS domain needed the trust.
    had_behind_proxy = bench.bench_config.certificate_for(domain).behind_proxy

    try:
        with spinner(output, f"Removing SSL certificate for {domain}"):
            bench.certificate_manager.remove_certificate_by_domain(domain)

        # Same rule as the add path: only the site's own canonical name moves `host_name`.
        # Removing an ALIAS certificate used to do double damage -- it both renamed the site to
        # the alias and downgraded its canonical URL to http.
        served = _site_serving(bench, domain)
        try:
            if served == domain:
                bench.set_bench_site_config(served, {"host_name": f"http://{domain}"})
                output.debug(f"Updated host_name to http://{domain} on {served}")
            elif served:
                output.debug(f"{domain} is an alias of {served}; leaving host_name alone")
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

        # `remove_certificate_by_domain` mutates the same list `bench.bench_config.ssl_certificates`
        # points at, so this reads the POST-removal set: whether gunicorn still legitimately needs
        # the trust for another --behind-proxy domain the bench still serves. If nothing does, the
        # wrapper must be rewritten and the operator told to restart, or gunicorn keeps trusting a
        # forwarded proto no certificate is asking for anymore -- the exact spoofable state the
        # mixed-bench guard on add exists to prevent in the first place.
        still_needed = any(cert.behind_proxy for cert in bench.bench_config.ssl_certificates)
        if had_behind_proxy and not still_needed and _regenerate_bench_supervisor_config(bench, output):
            if bench.bench_config.environment_type == FMBenchEnvType.prod:
                output.print(
                    f"Run 'fm restart {benchname}' to drop the forwarded-proto trust from gunicorn "
                    "(the compose converge above does not reach it).",
                    emoji_code=":information:",
                )
            else:
                # See the matching branch in _add_bench_certificate: a dev-environment bench never
                # actually ran gunicorn with this trust active, so there is nothing live to drop.
                output.print(
                    f"'{benchname}' is a dev-environment bench (runs 'bench serve', not gunicorn), so "
                    "the forwarded-proto trust just removed from the wrapper was never active.",
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
