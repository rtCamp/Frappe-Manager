from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench


@example(
    "Reset a site to a fresh install",
    "{benchname}",
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
    benchname: BenchNameArgument = None,
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
    Destroy a site: drop its database and reinstall every app, losing all site data.

    Only sites on the database server fm owns can be reset. A bench with its own \\[database] entry is refused, because that schema is not fm's to drop.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if not yes:
        # The bench is loaded first, so a mistyped name fails as "not found"
        # rather than offering to destroy whatever it did resolve to. Without a
        # TTY prompt_ask raises NonInteractiveError naming --yes: refusing is the
        # only safe answer when nobody is there to read the question.
        output.warning(
            f"Resetting '{bench.name}' drops its database and reinstalls every app. Every site record, file and customisation is lost, and there is no undo.",
        )
        choice = output.prompt_ask(
            prompt=f"🤔 Do you want to reset [bold][fm.ok]'{bench.name}'[/bold][/fm.ok]",
            choices=["yes", "no"],
            default="no",
            required_flag="--yes or -y",
        )
        if choice != "yes":
            output.print("Cancelled.", emoji_code=":x:")
            raise typer.Exit(0)

    with spinner(output, f"Resetting {benchname}"):
        bench.reset(admin_pass)
