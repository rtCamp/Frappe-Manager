from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchSiteArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench


@example(
    "Reset a site to a fresh install",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Reset one named site of a bench that serves several",
    "{benchname}/shop.example.com",
    detail="Only that site is reinstalled. The bench and its other sites keep running and keep their data.",
    benchname="mybench",
)
@example(
    "Reset and set a new administrator password",
    "{benchname} --admin-pass 'new-password'",
    benchname="mybench",
)
@example(
    "Reset unattended",
    "{benchname} --yes",
    detail="Skips the confirmation. Nothing else about the reset changes.",
    benchname="mybench",
)
def reset(
    ctx: typer.Context,
    address: BenchSiteArgument = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Reset without the confirmation. The site data is gone either way."),
    ] = False,
    admin_pass: Annotated[
        str | None,
        typer.Option(
            help="Administrator password for the reinstalled site. Taken from site_config.json, then common_site_config.json, or prompted for, when omitted."
        ),
    ] = None,
):
    """
    Destroy one site: drop its database and reinstall every app, losing all site data.

    The address picks the site. fm reset BENCH resets the bench's own site; fm reset BENCH/SITE resets exactly SITE and leaves the bench's other sites alone.

    Only a site whose database is on the server fm owns can be reset. A site with its own \\[database] entry is refused, because that schema is not fm's to drop.
    """

    check_bench_migration_required(address)

    services_manager = ctx.obj["services"]

    output = get_global_output_handler()
    bench = Bench.get_object(address, services_manager, output_handler=output)

    # The address is what picks the site: `BENCH/SITE` names one, a bare `BENCH` falls back to the
    # bench's own site. Everything below reads THIS name, so the site fm warns about, asks about and
    # refuses over is the same site it reinstalls. The messages used to interpolate the BENCH, which
    # is a different string the moment a bench serves a site not named after it, and outright
    # misleading on a bench serving several: it warned about 'shop' while about to drop one schema.
    named_site = ctx.obj.get("site") if ctx.obj else None
    site = named_site or bench.site_name

    # Ahead of the confirmation and regardless of --yes: asking a question whose only honourable
    # answer fm cannot carry out wastes the operator's consent. `external_database_config` is the one
    # place that decision is made, and it is keyed on the SITE being reinstalled rather than on the
    # bench, so a bench holding one global-db site and one external site resets the first and refuses
    # the second. Presence of the entry is the whole switch, exactly as for the schema `fm delete`
    # declines to drop.
    external_db = bench.external_database_config(site)
    if external_db is not None:
        output.display_error(
            f"Refusing to reset '{site}': its database '{external_db.name}' lives on '{external_db.host}', a server fm does not own. `bench reinstall` drops and recreates the schema, and that schema is not fm's to drop.",
        )
        raise typer.Exit(1)

    if not yes:
        # The bench is loaded first, so a mistyped name fails as "not found"
        # rather than offering to destroy whatever it did resolve to. Without a
        # TTY prompt_ask raises NonInteractiveError naming --yes: refusing is the
        # only safe answer when nobody is there to read the question.
        output.warning(
            f"Resetting '{site}' drops its database and reinstalls every app. Every site record, file and customisation is lost, and there is no undo.",
        )
        choice = output.prompt_ask(
            prompt=f"🤔 Do you want to reset [bold][fm.ok]'{site}'[/bold][/fm.ok]",
            choices=["yes", "no"],
            default="no",
            required_flag="--yes or -y",
        )
        if choice != "yes":
            output.print("Cancelled.", emoji_code=":x:")
            raise typer.Exit(0)

    with spinner(output, f"Resetting {site}"):
        bench.reset(admin_pass, site=site)
