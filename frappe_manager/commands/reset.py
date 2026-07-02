from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback


@example(
    "Drop database and reinstall all apps",
    "{benchname}",
    detail="Drops the site's database and reinstalls all apps; destructive and intended for development or recovery.",
    benchname="mybench",
)
@example(
    "Reset with custom admin password",
    "{benchname} --admin-pass newpassword",
    detail="Resets the bench and sets the new administrator password after reinstalling apps.",
    benchname="mybench",
)
def reset(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ] = None,
    admin_pass: Annotated[
        str | None,
        typer.Option(help="Password for the 'Administrator' User."),
    ] = None,
):
    """
    Drop database and reinstall all apps.

    Intended for resetting a site to a clean state; this operation removes site data.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]

    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    with spinner(output, f"Resetting {benchname}"):
        bench.reset(admin_pass)
