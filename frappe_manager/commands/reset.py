import typer
from typing import Annotated, Optional
from frappe_manager.site_manager.bench import Bench
from frappe_manager.utils.callbacks import sites_autocompletion_callback, sitename_callback

from frappe_manager.commands import app

@app.command()
def reset(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    admin_pass: Annotated[
        Optional[str],
        typer.Option(help="Password for the 'Administrator' User."),
    ] = None,
):
    """Reset bench site and reinstall all installed apps."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)
    bench.reset(admin_pass)
