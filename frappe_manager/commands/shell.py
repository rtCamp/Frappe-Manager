import typer
from typing import Annotated, Optional
from frappe_manager import SiteServicesEnum
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sites_autocompletion_callback, sitename_callback

from frappe_manager.commands import app

@app.command()
def shell(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    user: Annotated[Optional[str], typer.Option(help="Connect as this user.", show_default=False)] = None,
    service: Annotated[
        SiteServicesEnum, typer.Option(help="Specify compose service name for which to spawn shell.")
    ] = SiteServicesEnum.frappe,
):
    """Spawn shell for the give bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)
    bench.shell(SiteServicesEnum(service).value, user)
