import typer
from typing import Annotated, Optional
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sites_autocompletion_callback, sitename_callback

from frappe_manager.commands import app

@app.command()
def info(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
):
    """Shows information about given bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)
    bench.info()
