import typer
from typing import Annotated, Optional
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sites_autocompletion_callback, sitename_callback

def restart(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    web: Annotated[
        bool,
        typer.Option(help="Restart web service i.e socketio and frappe server."),
    ] = True,
    workers: Annotated[
        bool,
        typer.Option(help="Restart worker services i.e schedule and all workers."),
    ] = True,
    redis: Annotated[
        bool,
        typer.Option(help="Restart redis services."),
    ] = False,
):
    """Restart bench services."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)

    if web:
        bench.restart_web_containers_services()

    if workers:
        bench.restart_workers_containers_services()

    if redis:
        bench.restart_redis_services_containers()
