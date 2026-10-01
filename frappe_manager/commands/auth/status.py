from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import JsonResultOption
from frappe_manager.commands.auth._helpers import ADDRESS_HELP, _web_enforcement, build_auth_card, resolve_scope
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.callbacks import bench_site_autocompletion_callback, bench_site_callback


@example(
    "Show which surfaces of a bench ask for a password",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Show one site's own auth",
    "{benchname}/b.example.com",
    detail="A site with no auth of its own reports the bench's, and says so.",
    benchname="mybench",
)
def status(
    ctx: typer.Context,
    address: Annotated[
        str | None,
        typer.Argument(
            metavar="BENCH(/SITE)",
            # NOT the shared `BenchSiteArgument` help. There a bare bench means the bench's primary
            # site; here it means the WHOLE bench, and an operator who read "primary site is used"
            # would think the answer covered only one site.
            help=ADDRESS_HELP,
            autocompletion=bench_site_autocompletion_callback,
            callback=bench_site_callback,
        ),
    ] = None,
    json_results: JsonResultOption = False,
):
    """
    Report which surfaces are protected, with the credentials and allow lists while a surface is protected.

    Writes nothing. A site with no auth of its own follows the bench, and is reported as inherited.
    """

    output = get_global_output_handler()
    if json_results:
        output.set_json_results()
    bench, site, entry = resolve_scope(ctx, address, output)

    scope = f"{bench.name}/{site}" if site else bench.name
    stored = entry.auth if entry is not None else bench.bench_config.auth

    if output.wants_structured_data:
        # The EFFECTIVE config, not the stored one: a site with no auth of its own is protected or
        # not by the bench's setting, and a payload reporting `null` there would read as "open".
        effective = stored if stored is not None else (bench.bench_config.auth_for(site) if site else None)
        # `web` stays what fm RECORDS, so the key does not change meaning under an automation that
        # already reads it. `web_enforced` is what nginx actually serves, and the two disagree
        # exactly when the conf backing the record is missing or cannot be rendered: reporting only
        # the record told a caller a scope was protected while it served 200.
        enforced = _web_enforcement(bench, site, entry, effective)[0] if effective else False
        output.print_data(
            {
                "scope": scope,
                "site": site,
                "inherited": site is not None and entry is not None and entry.auth is None,
                "web": bool(effective and effective.web),
                "tools": bool(effective and getattr(effective, "tools", False)),
                "web_enforced": enforced,
                "user": getattr(effective, "user", None),
                "password": getattr(effective, "password", None),
                "allow_ips": list(getattr(effective, "allow_ips", None) or []),
                "allow_paths": list(getattr(effective, "allow_paths", None) or []),
                "sites_with_own_auth": bench.bench_config.sites_with_own_auth if not site else [],
            }
        )
        return

    if stored is None:
        if site:
            # Not "unconfigured": the site IS protected or not, by the bench's setting. Report
            # what it actually serves, and say where the answer came from.
            card = build_auth_card(
                bench,
                site,
                entry,
                scope,
                bench.bench_config.auth_for(site),
                hint_when_off=True,
                source=f"inherited from bench '{bench.name}'",
            )
            card.fact("own auth", f"fm auth enable {scope} --web")
            output.print_data(card.render())
            return
        from frappe_manager.output_manager import railcard

        # The model defaults, never written to `bench_config.toml`: no [auth] table exists yet, so
        # there is nothing recorded to check enforcement against -- this is what a bench gets
        # before `fm auth enable` ever runs, not a state that can drift from what nginx serves.
        card = railcard.Card(scope, "not configured; bench defaults apply", active=True)
        card.fact("tools", "protected (default)")
        card.fact("web", "open (default)")
        card.fact("mint credentials", f"fm auth enable {bench.name} --web")
        output.print_data(card.render())
        return

    card = build_auth_card(
        bench,
        site,
        entry,
        scope,
        stored,
        hint_when_off=True,
        source=f"its own, overriding bench '{bench.name}'" if site else None,
    )

    if not site and bench.bench_config.sites_with_own_auth:
        # A bench-level answer that omits a site with its own auth is worse than no answer: the
        # operator asked what this bench protects and was told about the bench's surfaces only, so
        # a protected site read as unprotected unless they already knew to ask for it by name.
        #
        # It says whether the site's web surface is actually PROTECTED, not just that an override
        # exists: `fm auth disable` leaves the entry in place with `web` off, and "has its own
        # auth" on that reads as protected when the site is deliberately open. A site owns only the
        # web surface -- the admin tools are one container pair for the whole bench.
        card.section("sites with their own auth")
        for name in bench.bench_config.sites_with_own_auth:
            site_entry = bench.bench_config.sites[name]
            state = "web protected" if site_entry.auth.web else "web open"
            card.fact(name, state)

    output.print_data(card.render())
