from contextlib import nullcontext
from typing import Annotated

import typer
from click.core import ParameterSource
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.bench_config import WorkersConfig
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.process_lock import bench_lock

_PANEL_SCOPE = "Scope (which services)"
_PANEL_CARE = "Care (what happens to in-flight work)"
_PANEL_ADVANCED = "Advanced"


@example(
    "Restart web and workers",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Restart workers only",
    "{benchname} --workers --no-web",
    benchname="mybench",
)
@example(
    "Restart without waiting for in-flight jobs",
    "{benchname} --no-drain",
    detail="Interrupted jobs land in the failed-jobs registry.",
    benchname="mybench",
)
@example(
    "Restart one service",
    "{benchname} --service socketio",
    detail="Repeatable, and it skips the drain.",
    benchname="mybench",
)
@example(
    "Zero-downtime web restart",
    "{benchname} --rolling",
    benchname="mybench",
)
@bench_lock(operation="restart")
def restart(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    web: Annotated[
        bool,
        typer.Option(
            help="Restart the web tier (frappe and socketio).",
            rich_help_panel=_PANEL_SCOPE,
        ),
    ] = True,
    workers: Annotated[
        bool,
        typer.Option(
            help="Restart the worker tier (schedule and the RQ workers).",
            rich_help_panel=_PANEL_SCOPE,
        ),
    ] = True,
    redis: Annotated[
        bool,
        typer.Option(
            help="Restart redis too; this briefly disconnects every consumer.",
            rich_help_panel=_PANEL_SCOPE,
        ),
    ] = False,
    nginx: Annotated[
        bool,
        typer.Option(
            help="Restart the bench nginx service, e.g. after a proxy or TLS config change.",
            rich_help_panel=_PANEL_SCOPE,
        ),
    ] = False,
    recreate: Annotated[
        bool,
        typer.Option(
            "--recreate",
            help="Restart whole containers instead of the supervisor processes inside them: slower, and every consumer of the restarted service reconnects. The bench nginx is restarted with them, because it resolves its upstreams once and caches the addresses a recreate changes.",
            rich_help_panel=_PANEL_ADVANCED,
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Kill everything fast instead of restarting it gracefully. Implies --no-drain; conflicts with --drain and --rolling.",
            rich_help_panel=_PANEL_CARE,
        ),
    ] = False,
    rolling: Annotated[
        bool,
        typer.Option(
            "--rolling",
            help="Zero-downtime recreate of the web tier on the current image tag; image benches only. Web-only, so it conflicts with --redis, --nginx and --no-web.",
            rich_help_panel=_PANEL_CARE,
        ),
    ] = False,
    drain: Annotated[
        bool,
        typer.Option(
            "--drain/--no-drain",
            help="Wait for in-flight RQ jobs before restarting workers, and abort the restart if they outlast \\[workers].drain_timeout.",
            rich_help_panel=_PANEL_CARE,
        ),
    ] = True,
    service: Annotated[
        list[str],
        typer.Option(
            "--service",
            help="Restart only the named service (repeatable); overrides the group flags and skips the drain.",
            show_default=False,
            rich_help_panel=_PANEL_SCOPE,
        ),
    ] = [],
):
    """
    Restart bench services: web and workers by default, redis and nginx on request.

    Workers drain first: fm waits up to \\[workers].drain_timeout for in-flight jobs, and rather than kill a job that overruns it resumes the workers and aborts the restart before any service is touched. --no-drain skips the wait and interrupts running jobs, --force kills everything fast, and a run naming --service skips the drain as well.

    Restart bounces what is already running, and a stopped bench is fm start's job: unlike a restart, start also reconciles the bench nginx config and the global proxy entry.
    """

    output = get_global_output_handler()

    # Pure flag-conflict guards run before any bench lookup.
    drain_explicit = ctx.get_parameter_source("drain") == ParameterSource.COMMANDLINE

    if rolling and recreate:
        output.error("--rolling cannot be combined with --recreate", exception=typer.Exit(code=1))

    if force and rolling:
        output.error(
            "--force cannot be combined with --rolling (the rolling swap replaces web containers gracefully)",
            exception=typer.Exit(code=1),
        )

    if force and drain_explicit and drain:
        output.error(
            "--force cannot be combined with --drain (drain waits for in-flight jobs; force kills them)",
            exception=typer.Exit(code=1),
        )

    if drain_explicit and drain and not workers:
        output.error(
            "--drain cannot be combined with --no-workers (drain suspends the worker tier "
            "before restarting it; with workers excluded there is nothing to drain)",
            exception=typer.Exit(code=1),
        )

    if service:
        if rolling:
            output.error("--service cannot be combined with --rolling", exception=typer.Exit(code=1))
        if drain_explicit and drain:
            output.error(
                "--service cannot be combined with --drain (drain applies to the worker group: --workers --drain)",
                exception=typer.Exit(code=1),
            )

    # --rolling swaps the web containers and, at most, cycles the workers afterwards: its branch
    # returns before the redis, nginx and web legs below, so a scope flag it cannot honour is
    # refused here instead of being silently dropped.
    if rolling:
        if redis:
            output.error(
                "--redis cannot be combined with --rolling (the rolling swap only replaces web containers; "
                "bounce redis in its own run: fm restart <bench> --redis --no-web --no-workers)",
                exception=typer.Exit(code=1),
            )
        if nginx:
            output.error(
                "--nginx cannot be combined with --rolling (the rolling swap only replaces web containers; "
                "restart nginx in its own run: fm restart <bench> --nginx --no-web --no-workers)",
                exception=typer.Exit(code=1),
            )
        if not web:
            output.error(
                "--no-web cannot be combined with --rolling (--rolling IS the zero-downtime web restart)",
                exception=typer.Exit(code=1),
            )

    # Nothing selected is a usage error, not a successful no-op: an empty selection would
    # otherwise silently skip the server health check and exit 0. --service replaces the group
    # flags, so it is exempt.
    if not service and not (web or workers or redis or nginx):
        output.error(
            "--no-web with --no-workers leaves nothing to restart: add --redis or --nginx, "
            "or name a service with --service",
            exception=typer.Exit(code=1),
        )

    # Targeted restarts are surgical and force means kill-fast: both imply no drain.
    if force or service:
        drain = False

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]

    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    from frappe_manager.site_manager.modules.deploy_orchestrator import (
        DeployError,
        DeployOrchestrator,
    )
    from frappe_manager.site_manager.modules.worker_drain import rq_suspended

    orchestrator = DeployOrchestrator(bench, output_handler=output)

    # `fm restart` bounces what is RUNNING; starting a bench is `fm start`, which also reconciles
    # the bench nginx conf and the global proxy entry that a bounce deliberately leaves alone.
    # `systemctl try-restart` answers this the same way: something deliberately stopped is not
    # resurrected by a restart.
    if not bench.running:
        output.error(
            f"Bench '{benchname}' is not fully running, and restart only bounces what is: "
            f"use 'fm start {benchname}'.",
            exception=typer.Exit(code=1),
        )

    # `fm restart`'s default leg re-execs each supervisor program's `command=` line fresh from
    # disk, so it is the command that APPLIES a changed gunicorn wrapper -- but only if the file
    # on disk is current. Nothing else rewrites it from the host's trusted-proxy set (see
    # `fm services trusted-proxies`), so without this a front added or removed never reaches
    # gunicorn and it keeps trusting, or ignoring, a forwarded scheme forever.
    try:
        bench.supervisor.setup_supervisor(bench.path, force=True)
    except Exception as e:
        output.warning(f"Could not refresh the supervisor configuration ({e}); restarting what is on disk")

    def _restart_workers(use_container_restart: bool) -> None:
        if not drain:
            kill_timeout = (bench.bench_config.workers or WorkersConfig()).kill_timeout
            output.warning(
                f"Restarting workers WITHOUT draining: in-flight jobs are interrupted "
                f"(SIGUSR1, force-stop after {kill_timeout}s)"
            )
        bench.restart_workers_containers_services(use_container_restart=use_container_restart, force=force)

    if service:
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
                    if recreate:
                        bench.workers.docker_client.compose.restart(services=[svc], timeout=0 if force else 100)
                    else:
                        bench.restart_supervisor_service(
                            svc, docker_client_obj=bench.workers.docker_client, force=force
                        )
                elif svc in supervised and not recreate:
                    bench.restart_supervisor_service(svc, force=force)
                else:
                    bench.docker_ops.restart_services([svc], force=force)
                output.print(f"Restarted {svc}")

            # Same trap as the grouped path (`Bench.restart_web_containers_services`): nginx
            # caches its upstream addresses at config parse, so recreating the containers they
            # name leaves it proxying to addresses nothing answers on. Only when a CONTAINER
            # moved, and never when nginx was restarted in the same run anyway.
            moved_upstreams = recreate and {"frappe", "socketio"} & set(service)
            if moved_upstreams and "nginx" not in service:
                output.change_head("Restarting nginx to pick up the new container addresses")
                bench.restart_nginx_service(force=force)

            if {"frappe", "nginx"} & set(service):
                try:
                    bench.orchestrator.verify_bench_server_responding()
                    # Through the proxy as well: the check above asks the app about itself and
                    # cannot see an nginx that is down or pointed at the wrong addresses.
                    bench.orchestrator.verify_site_reachable_through_nginx()
                except Exception as e:
                    output.display_error(f"Restart completed but the site is not being served: {e}")
                    raise typer.Exit(1) from e
        return

    if rolling:
        from frappe_manager.site_manager.bench_config import BenchRuntime

        if bench.bench_config.runtime != BenchRuntime.image:
            output.error(
                "--rolling needs an image bench (mount benches restart web via supervisor, which is already fast)",
                exception=typer.Exit(code=1),
            )
        # `rq_suspended` owns the resume, signals included: `rq:suspended` is a redis key that
        # outlives this process, so a leaked one leaves workers alive and consuming nothing.
        suspend = rq_suspended(orchestrator, output, action="restart") if (workers and drain) else nullcontext()
        with suspend:
            try:
                orchestrator.rolling_restart()
            except DeployError as e:
                output.display_error(str(e))
                raise typer.Exit(1) from e
            if workers:
                with spinner(output, f"Restarting workers for {benchname}"):
                    _restart_workers(use_container_restart=False)
        return

    use_container_restart = recreate

    with spinner(output, f"Restarting {benchname}"):
        # Gate first: on drain timeout the restart aborts before ANY leg
        # (web included) is touched.
        suspend = rq_suspended(orchestrator, output, action="restart") if (workers and drain) else nullcontext()
        with suspend:
            if web:
                bench.restart_web_containers_services(use_container_restart=use_container_restart, force=force)

            # Inside the drained window, before the workers come back: a redis
            # bounce after resume_workers() kills the jobs the just-resumed
            # workers picked up, voiding the guarantee the drain paid for.
            if redis:
                bench.restart_redis_services_containers()

            if workers:
                _restart_workers(use_container_restart=use_container_restart)

        if nginx:
            bench.restart_nginx_service(force=force)

        # A restart that leaves the site dead must not exit 0. Both halves are needed for that to
        # be true: the first asks the app, the second asks the proxy that actually serves it, and
        # only the second can see an nginx that is down or holding its upstreams' old addresses.
        if web or nginx:
            try:
                if web:
                    bench.orchestrator.verify_bench_server_responding()
                bench.orchestrator.verify_site_reachable_through_nginx()
            except Exception as e:
                output.display_error(f"Restart completed but the site is not being served: {e}")
                raise typer.Exit(1) from e
