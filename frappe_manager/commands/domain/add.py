"""Add alias domain command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchSiteArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.domain_conflict import DomainConflictError, validate_domains_unique
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.site import is_fqdn, is_wildcard_fqdn

# The only rich_help_panel this command needs; a single-panel `--help` gets no visible panel
# title, same rule `test_option_scope_panels.py` enforces for `create`/`update`.
_PANEL_DOMAIN = "Domain Options"


def _validate_domain_arguments(value: list[str]) -> list[str]:
    """Validate each `DOMAIN` positional the same way `--add-alias` always has.

    Adapted from `alias_domains_validation_callback` (`utils/callbacks.py:717`) for a variadic
    argument: click has already split the values on spaces, so there is no comma-string left to
    parse, only each token's FQDN shape and the set's uniqueness to check.
    """
    validated: list[str] = []
    for domain in value:
        domain = domain.strip()
        if not domain:
            continue
        if domain.startswith("*."):
            if not is_wildcard_fqdn(domain):
                raise typer.BadParameter(
                    f"Invalid wildcard domain '{domain}'. Wildcard domains must be in format '*.example.com'."
                )
        else:
            if not is_fqdn(domain) or "." not in domain:
                raise typer.BadParameter(
                    f"Invalid domain '{domain}'. Domain must be a valid FQDN with a TLD (e.g., 'example.com')."
                )
        validated.append(domain)

    if len(validated) != len(set(validated)):
        raise typer.BadParameter("Duplicate domains found in the given list.")

    return validated


@example(
    "Add an alias domain to a bench's primary site",
    "{benchname} www.example.com",
    detail="No certificate is issued yet; run fm ssl add afterwards.",
    benchname="mybench",
)
@example(
    "Add several aliases in one call",
    "{benchname} www.example.com api.example.com",
    benchname="mybench",
)
@example(
    "Add an alias to one site of a multi-site bench",
    "{benchname}/shop.example.com www.shop.example.com",
    benchname="mybench",
)
@example(
    "Add a domain another bench already serves, deliberately",
    "{benchname} shared.example.com --allow-domain-conflicts",
    benchname="mybench",
)
def add_domain(
    ctx: typer.Context,
    address: BenchSiteArgument = None,
    domains: Annotated[
        list[str],
        typer.Argument(
            metavar="DOMAIN...",
            help="Alias domains to add to the site, e.g. www.example.com api.example.com.",
            callback=_validate_domain_arguments,
        ),
    ] = None,
    allow_domain_conflicts: Annotated[
        bool,
        typer.Option(
            "--allow-domain-conflicts",
            help="Skip the uniqueness check against every other bench's domains.",
            rich_help_panel=_PANEL_DOMAIN,
        ),
    ] = False,
):
    """
    Add alias domains to a bench's site.

    A bare BENCH attaches the aliases to its primary site; name a site with BENCH/SITE when the bench serves more than one. 'all' is refused here -- an alias belongs to one site.

    No certificate is issued for a new alias; run fm ssl add BENCH/DOMAIN afterwards.
    """
    output = get_global_output_handler()
    check_bench_migration_required(address)

    fm_config = ctx.obj["fm_config_manager"]
    services_manager = ctx.obj["services"]
    site = ctx.obj.get("site") if ctx.obj else None

    bench = Bench.get_object(address, services_manager, output_handler=output)

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    skip_check = allow_domain_conflicts or not fm_config.validation.enforce_domain_uniqueness
    try:
        validate_domains_unique(
            domains,
            benches_root=CLI_BENCHES_DIRECTORY,
            exclude_bench=bench.name,
            skip_check=skip_check,
        )
    except DomainConflictError as e:
        output.display_error(str(e))
        output.print("\nTo proceed anyway, use: --allow-domain-conflicts", emoji_code="")
        raise typer.Exit(1) from e

    output.change_head("Updating alias domains")
    # `update_alias_domains` (site_manager/modules/bench_orchestrator.py:1376) saves the config,
    # reloads nginx and prints the "fm ssl add" follow-up hint for each newly added domain itself.
    bench.update_alias_domains(add_domains=domains, site=site)
    output.print("Alias domains updated successfully")
