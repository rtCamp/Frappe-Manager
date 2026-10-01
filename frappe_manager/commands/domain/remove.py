import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchServedDomainArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.utils.site import resolve_known_name


@example(
    "Remove an alias from a bench",
    "{benchname}/www.example.com",
    benchname="mybench",
)
def remove_domain(
    ctx: typer.Context,
    address: BenchServedDomainArgument = None,
):
    """
    Remove an alias domain from whichever site of the bench serves it.

    Takes the DOMAIN you are removing, not the site it belongs to: fm looks up which site serves it. That is the mirror of fm domain add, which takes the SITE you are adding to, because the domain does not exist yet to be named. Same rule as fm ssl remove BENCH/DOMAIN.
    """
    output = get_global_output_handler()

    # The `fm domain add BENCH/SITE DOMAIN` shape, typed at a removal. Click's own error names
    # the stray token and nothing else, while the command the operator meant is derivable from
    # exactly what they typed.
    extra = [arg for arg in (ctx.args or []) if not arg.startswith("-")]
    if extra:
        bench = (address or "").split("/")[0]
        output.display_error(
            f"fm domain remove takes one address, the domain itself: fm domain remove BENCH/DOMAIN. "
            f"Did you mean 'fm domain remove {bench}/{extra[0]}'?"
        )
        raise typer.Exit(1)

    check_bench_migration_required(address)

    services_manager = ctx.obj["services"]
    domain = ctx.obj.get("domain") if ctx.obj else None

    bench = Bench.get_object(address, services_manager, output_handler=output)

    if not domain:
        output.display_error(f"fm domain remove needs a domain: use {bench.name}/DOMAIN.")
        raise typer.Exit(1)

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    # A domain is served either as a site's own canonical name or as one of that site's
    # `alias_domains` (site_manager/bench_config.py:1526). Only the alias form is removable here;
    # the canonical form mirrors the refusal `update_alias_domains` itself raises when asked to
    # remove a site's own name (site_manager/modules/bench_orchestrator.py:1413-1415).
    sites = bench.bench_config.sites or {}
    # The one address rule, against the aliases this bench actually serves.
    domain = resolve_known_name(domain, [a for e in sites.values() for a in (e.alias_domains or [])]) or domain
    owner_site = next(
        (name for name, entry in sites.items() if domain in (entry.alias_domains or [])),
        None,
    )

    if owner_site is None:
        if resolve_known_name(domain, sites) in sites:
            output.display_error(
                f"'{domain}' is the site's own domain, not an alias; fm domain remove only drops aliases."
            )
        else:
            known = ", ".join(f"'{s}'" for s in sorted(sites)) or "no sites"
            output.display_error(f"'{domain}' is not a served alias of bench '{bench.name}'. It serves {known}.")
        raise typer.Exit(1)

    # A certificate outlives the domain it was issued for unless something deals with it here:
    # nginx stops answering immediately, but the material and its private key stay on disk, and
    # re-adding the domain puts THAT certificate back in service with no issuance step.
    #
    # Split by type because the cost of being wrong differs by orders of magnitude. A dev
    # certificate is signed by a CA fm owns and regenerates in seconds, so removing it with the
    # domain costs nothing. A Let's Encrypt one costs a rate-limited issuance, and a `--custom` one
    # is bytes only the operator holds -- fm never stored the --cert/--key paths, so it cannot be
    # re-created at all. Those two are refused and named, which is the order NPM documents and
    # certbot enforces.
    cert = next((c for c in bench.certificate_manager.certificates if c.domain == domain), None)
    if cert is not None:
        if cert.ssl_type == SUPPORTED_SSL_TYPES.dev:
            output.change_head(f"Removing the dev certificate for {domain}")
            bench.certificate_manager.remove_certificate_by_domain(domain)
            output.print(f"Removed the dev certificate for {domain}; it regenerates if you add the domain back")
        else:
            output.display_error(
                f"'{domain}' holds a {cert.ssl_type.value} certificate, which fm will not discard on your behalf: "
                f"a Let's Encrypt one costs a rate-limited reissue and a custom one fm cannot recreate, "
                f"since it stores the bytes and never the files you imported."
            )
            output.print(
                f"Remove it first: 'fm ssl remove {bench.name}/{domain}', then run this again.", emoji_code=""
            )
            raise typer.Exit(1)

    output.change_head("Updating alias domains")
    bench.update_alias_domains(remove_domains=[domain], site=owner_site)
    output.print("Alias domains updated successfully")
