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
    "Restart one service only",
    "{benchname} --service socketio",
    detail="Targets a single service instead of a group; repeat --service for several. "
    "Code services restart via supervisor, infra services (nginx, redis) via container.",
    benchname="mybench",
)
@example(
    "Restart via container restart",
    "{benchname} --container",
    detail="Restarts by restarting the entire Docker containers (slower but thorough).",
    benchname="mybench",
)
@example(
    "Zero-downtime web restart (image bench)",
    "{benchname} --rolling",
    detail="Recreates web containers on the current tag via the deploy engine's rolling swap: "
    "new replicas serve before old ones drain.",
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
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Force restart: kills processes (default mode) / stops containers with timeout=0 (--container).",
        ),
    ] = False,
    rolling: Annotated[
        bool,
        typer.Option(
            "--rolling",
            help="Zero-downtime web-tier recreate on the current image tag (image benches only).",
        ),
    ] = False,
    drain: Annotated[
        bool,
        typer.Option(
            "--drain",
            help="Wait for in-flight RQ jobs to finish before restarting workers (graceful).",
        ),
    ] = False,
    service: Annotated[
        list[str],
        typer.Option(
            "--service",
            help="Restart only the named service(s) (repeatable); overrides the group flags. "
            "Any service from the bench or workers compose.",
            show_default=False,
        ),
    ] = [],
):
    """
    Restart bench services. Web and workers by default; redis/nginx are opt-in: rarely needed, and a redis restart briefly disconnects every consumer (in-flight jobs can fail; data itself persists via volumes + RDB).

    Three modes: in-container process restart via supervisor (default, fastest), --container (full container stop/start, thorough), --rolling (zero-downtime web recreate; image benches).
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError, DeployOrchestrator

    orchestrator = DeployOrchestrator(bench, output_handler=output)

    if rolling and container:
        output.error("--rolling cannot be combined with --container", exception=typer.Exit(code=1))

    # Supervisor-level restart execs into running containers; a stopped bench
    # needs start (or --container, which starts containers as it restarts them).
    if not container and not bench.running:
        output.error(
            f"Bench '{benchname}' is not fully running; use 'fm start {benchname}' "
            f"(or 'fm restart --container' to restart-and-start containers).",
            exception=typer.Exit(code=1),
        )

    def _restart_workers(use_container_restart: bool) -> None:
        """Worker restart, optionally draining in-flight jobs first.

        The RQ suspend flag lives in redis, so workers restarted mid-drain come
        back suspended until resume -- ordering is safe even across the restart.
        """
        if drain:
            orchestrator.drain_workers()
        try:
            bench.restart_workers_containers_services(use_container_restart=use_container_restart, force=force)
        finally:
            if drain:
                orchestrator.resume_workers()

    if service:
        if rolling:
            output.error("--service cannot be combined with --rolling", exception=typer.Exit(code=1))
        if drain:
            output.error(
                "--service cannot be combined with --drain (drain applies to the worker group: --workers --drain)",
                exception=typer.Exit(code=1),
            )
        bench_services = set(bench.compose_file_manager.get_services_list())
        try:
            worker_services = (
                set(bench.workers.compose_file_manager.get_services_list())
                if bench.workers.compose_file_manager.exists()
                else set()
            )
        except Exception:
            worker_services = set()
        unknown = [s for s in service if s not in bench_services | worker_services]
        if unknown:
            output.error(
                f"Unknown service(s): {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(bench_services | worker_services))}",
                exception=typer.Exit(code=1),
            )

        # Code services restart via supervisor (matching the group behavior);
        # infra services (nginx, redis, ...) have no supervisor programs.
        supervised = {"frappe", "socketio", "schedule"}
        with spinner(output, f"Restarting {len(service)} service(s)"):
            for svc in service:
                output.change_head(f"Restarting service - {svc}")
                if svc in worker_services:
                    if container:
                        bench.workers.docker_client.compose.restart(services=[svc], timeout=0 if force else 100)
                    else:
                        bench.restart_supervisor_service(svc, docker_client_obj=bench.workers.docker_client, force=force)
                elif svc in supervised and not container:
                    bench.restart_supervisor_service(svc, force=force)
                else:
                    bench.docker_ops.restart_services([svc], force=force)
                output.print(f"Restarted {svc}")

            if {"frappe", "nginx"} & set(service):
                try:
                    bench.orchestrator.verify_bench_server_responding()
                except Exception as e:
                    output.display_error(f"Restart completed but the bench server is not responding: {e}")
                    raise typer.Exit(1) from e
        return

    if rolling:
        from frappe_manager.site_manager.bench_config import BenchRuntime

        if bench.bench_config.runtime != BenchRuntime.image:
            output.error(
                "--rolling needs an image bench (mount benches restart web via supervisor, which is already fast)",
                exception=typer.Exit(code=1),
            )
        try:
            orchestrator.rolling_restart()
        except DeployError as e:
            output.display_error(str(e))
            raise typer.Exit(1) from e
        if workers:
            with spinner(output, f"Restarting workers for {benchname}"):
                _restart_workers(use_container_restart=False)
        return

    use_container_restart = container

    with spinner(output, f"Restarting {benchname}"):
        if web:
            bench.restart_web_containers_services(use_container_restart=use_container_restart, force=force)

        if workers:
            _restart_workers(use_container_restart=use_container_restart)

        if redis:
            bench.restart_redis_services_containers()

        if nginx:
            bench.restart_nginx_service(force=force)

        # A restart that leaves the site dead must not exit 0.
        if web:
            try:
                bench.orchestrator.verify_bench_server_responding()
            except Exception as e:
                output.display_error(f"Restart completed but the bench server is not responding: {e}")
                raise typer.Exit(1) from e
