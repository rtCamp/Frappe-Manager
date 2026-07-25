from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback


@example(
    "Restart web and workers (default)",
    "{benchname}",
    detail="Restarts both web and worker services for the bench. Safe for applying configuration changes.",
    benchname="mybench",
)
@example(
    "Restart via container restart",
    "{benchname} --container",
    detail="Restarts by restarting the entire Docker containers (slower but thorough).",
    benchname="mybench",
)
@example(
    "Restart via supervisor (faster)",
    "{benchname} --supervisor",
    detail="Uses supervisor to restart processes inside containers for a faster restart without recreating containers.",
    benchname="mybench",
)
@example(
    "Restart web services only",
    "{benchname} --web --no-workers",
    detail="Restarts only web-related services (frappe, socketio) without touching workers.",
    benchname="mybench",
)
@example(
    "Restart workers only",
    "{benchname} --workers --no-web",
    detail="Restarts worker processes (schedule, long/short workers) while leaving web services running.",
    benchname="mybench",
)
@example(
    "Force restart (immediate kill)",
    "{benchname} --force",
    detail="Performs an immediate kill and restart; use when processes are unresponsive.",
    benchname="mybench",
)
@example(
    "Restart redis services",
    "{benchname} --redis",
    detail="Restarts Redis instances used by the bench (cache and queue backends).",
    benchname="mybench",
)
@example(
    "Restart nginx service",
    "{benchname} --nginx",
    detail="Restarts the nginx service for the bench, useful after configuration changes to proxy or TLS.",
    benchname="mybench",
)
def restart(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
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
    """
    Restart bench services (web, workers, redis, nginx).

    Choose between container-level restarts or supervisor-level restarts for faster in-container restarts.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

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
