from typing import Annotated, Optional
import typer
from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import spinner, get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager.commands import check_bench_migration_required


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
    nginx: Annotated[
        bool,
        typer.Option(help="Restart nginx service."),
    ] = False,
    container: Annotated[
        bool,
        typer.Option(
            "--container",
            help="Restart entire Docker container(s). Stops and starts the container.",
        ),
    ] = False,
    supervisor: Annotated[
        bool,
        typer.Option(
            "--supervisor",
            help="Restart supervisor processes inside container. Faster than container restart.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Force restart: --supervisor uses stop+start (kills processes), --container uses timeout=0 (immediate kill).",
        ),
    ] = False,
):
    """Restart bench services (web, workers, redis, nginx)"""

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    use_container_restart = container
    use_supervisor_restart = supervisor

    if not use_container_restart and not use_supervisor_restart:
        use_supervisor_restart = True

    if use_container_restart and use_supervisor_restart:
        output.error("Cannot use both --container and --supervisor flags simultaneously", exception=typer.Exit(code=1))

    with spinner(output, f"Restarting {benchname}"):
        if web:
            bench.restart_web_containers_services(use_container_restart=use_container_restart, force=force)

        if workers:
            bench.restart_workers_containers_services(use_container_restart=use_container_restart, force=force)

        if redis:
            bench.restart_redis_services_containers()

        if nginx:
            bench.restart_nginx_service(force=force)
