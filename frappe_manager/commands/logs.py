import typer
from typing import Annotated, Optional
from frappe_manager import SiteServicesEnum
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sites_autocompletion_callback, sitename_callback

def logs(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    service: Annotated[
        Optional[SiteServicesEnum], typer.Option(help="Specify compose service name to show container logs.")
    ] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs.")] = False,
):
    """Show frappe server logs or container logs for a given bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)
    bench.logs(follow, service)
