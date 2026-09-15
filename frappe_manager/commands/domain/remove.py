"""Remove alias domain command."""

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchServedDomainArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.site import Bench


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

    Takes the address grammar, not an argument: once a domain exists it is addressable, the same way fm ssl remove BENCH/DOMAIN is. Creation takes arguments because the domain does not exist yet; removal takes the address.
    """
    output = get_global_output_handler()
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
    owner_site = next(
        (name for name, entry in sites.items() if domain in (entry.alias_domains or [])),
        None,
    )

    if owner_site is None:
        if domain in sites:
            output.display_error(
                f"'{domain}' is the site's own domain, not an alias; fm domain remove only drops aliases."
            )
        else:
            known = ", ".join(f"'{s}'" for s in sorted(sites)) or "no sites"
            output.display_error(f"'{domain}' is not a served alias of bench '{bench.name}'. It serves {known}.")
        raise typer.Exit(1)

    output.change_head("Updating alias domains")
    bench.update_alias_domains(remove_domains=[domain], site=owner_site)
    output.print("Alias domains updated successfully")
