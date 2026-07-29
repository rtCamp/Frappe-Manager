from typing import Annotated

import typer
from click.core import ParameterSource
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.bench_config import WorkersConfig
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback

# Rich help panels for `fm restart --help`, grouped by concern.
_PANEL_SCOPE = "Scope (which services)"
_PANEL_CARE = "Care (what happens to in-flight work)"
_PANEL_ADVANCED = "Advanced"


@example(
    "Restart web and workers (default)",
    "{benchname}",
    detail="Drains workers first (waits for in-flight jobs, bounded by [workers].drain_timeout), then restarts web and workers. Aborts rather than kill a job that does not finish in time.",
    benchname="mybench",
)
@example(
    "Restart without waiting for jobs",
    "{benchname} --no-drain",
    detail="Skips the drain wait and interrupts in-flight jobs explicitly (SIGUSR1, then force-stop after [workers].kill_timeout seconds); interrupted jobs land in the failed-jobs registry.",
    benchname="mybench",
)
@example(
    "Restart one service only",
    "{benchname} --service socketio",
    detail="Targets a single service instead of a group; repeat --service for several. Skips draining. Code services restart via supervisor, infra services (nginx, redis) via container.",
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
    detail="Immediate kill and restart for unresponsive processes. Skips draining; in-flight jobs are interrupted and marked failed or retried.",
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
        typer.Option(
            help="Restart the web tier (frappe server and socketio).",
            rich_help_panel=_PANEL_SCOPE,
        ),
    ] = True,
    workers: Annotated[
        bool,
        typer.Option(
            help="Restart the worker tier (schedule and all RQ workers).",
            rich_help_panel=_PANEL_SCOPE,
        ),
    ] = True,
    redis: Annotated[
        bool,
        typer.Option(
            help="Restart redis services (opt-in: briefly disconnects every consumer).",
            rich_help_panel=_PANEL_SCOPE,
        ),
    ] = False,
    nginx: Annotated[
        bool,
        typer.Option(
            help="Restart the bench nginx service (opt-in: useful after proxy or TLS config changes).",
            rich_help_panel=_PANEL_SCOPE,
        ),
    ] = False,
    container: Annotated[
        bool,
        typer.Option(
            "--container",
            help="Restart whole Docker containers instead of supervisor processes (slower, thorough; also starts a stopped bench).",
            rich_help_panel=_PANEL_ADVANCED,
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Kill everything fast: supervisor stop+start (default mode) or container stop with timeout=0 (--container). Implies --no-drain; conflicts with explicit --drain and --rolling.",
            rich_help_panel=_PANEL_CARE,
        ),
    ] = False,
    rolling: Annotated[
        bool,
        typer.Option(
            "--rolling",
            help="Zero-downtime web-tier recreate on the current image tag (image benches only). Workers still drain and cycle normally.",
            rich_help_panel=_PANEL_CARE,
        ),
    ] = False,
    drain: Annotated[
        bool,
        typer.Option(
            "--drain/--no-drain",
            help="Wait for in-flight RQ jobs to finish before restarting workers; abort the restart if they do not finish within \\[workers].drain_timeout. --no-drain skips the wait and interrupts running jobs.",
            rich_help_panel=_PANEL_CARE,
        ),
    ] = True,
    service: Annotated[
        list[str],
        typer.Option(
            "--service",
            help="Restart only the named service(s) (repeatable); overrides the group flags and skips draining. "
            "Any service from the bench or workers compose.",
            show_default=False,
            rich_help_panel=_PANEL_SCOPE,
        ),
    ] = [],
):
    r"""
    Restart bench services. Web and workers by default; redis/nginx are opt-in: rarely needed, and a redis restart briefly disconnects every consumer (in-flight jobs can fail; data itself persists via volumes + RDB).

    Workers drain by default: fm suspends them, waits for in-flight jobs (bounded by \[workers].drain_timeout), restarts, and resumes. If jobs do not finish in time the restart is ABORTED with workers resumed; nothing is killed implicitly. --no-drain interrupts running jobs explicitly; --force kills everything fast.

    Mechanisms: in-container process restart via supervisor (default, fastest), --container (full container stop/start, thorough; also starts a stopped bench), --rolling (zero-downtime web recreate; image benches).
    """

    output = get_global_output_handler()

    # Pure flag-conflict guards run before any bench lookup.
    drain_explicit = ctx.get_parameter_source("drain") == ParameterSource.COMMANDLINE

    if rolling and container:
        output.error("--rolling cannot be combined with --container", exception=typer.Exit(code=1))

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

    if service:
        if rolling:
            output.error("--service cannot be combined with --rolling", exception=typer.Exit(code=1))
        if drain_explicit and drain:
            output.error(
                "--service cannot be combined with --drain (drain applies to the worker group: --workers --drain)",
                exception=typer.Exit(code=1),
            )

    # Targeted restarts are surgical and force means kill-fast: both imply no drain.
    if force or service:
        drain = False

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError, DeployOrchestrator

    orchestrator = DeployOrchestrator(bench, output_handler=output)

    # Supervisor-level restart execs into running containers; a stopped bench
    # needs start (or --container, which starts containers as it restarts them).
    if not container and not bench.running:
        output.error(
            f"Bench '{benchname}' is not fully running; use 'fm start {benchname}' "
            f"(or 'fm restart --container' to restart-and-start containers).",
            exception=typer.Exit(code=1),
        )

    def _drain_gate() -> None:
        """Drain is a GATE: suspend workers and wait for in-flight jobs; on
        timeout resume the workers and abort the restart before any leg runs.

        The RQ suspend flag lives in redis, so workers restarted mid-drain come
        back suspended until resume -- ordering is safe even across the restart.
        """
        if orchestrator.drain_workers():
            return
        orchestrator.resume_workers()
        output.display_error(
            f"Drain timed out after {orchestrator.workers_config.drain_timeout}s: workers still busy. "
            "Restart aborted, workers resumed. Raise \\[workers].drain_timeout or use --no-drain to "
            "interrupt in-flight jobs."
        )
        raise typer.Exit(1)

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
        drained = False
        if workers and drain:
            _drain_gate()
            drained = True
        try:
            try:
                orchestrator.rolling_restart()
            except DeployError as e:
                output.display_error(str(e))
                raise typer.Exit(1) from e
            if workers:
                with spinner(output, f"Restarting workers for {benchname}"):
                    _restart_workers(use_container_restart=False)
        finally:
            if drained:
                orchestrator.resume_workers()
        return

    use_container_restart = container

    with spinner(output, f"Restarting {benchname}"):
        # Gate first: on drain timeout the restart aborts before ANY leg
        # (web included) is touched.
        drained = False
        if workers and drain:
            _drain_gate()
            drained = True
        try:
            if web:
                bench.restart_web_containers_services(use_container_restart=use_container_restart, force=force)

            if workers:
                _restart_workers(use_container_restart=use_container_restart)
        finally:
            if drained:
                orchestrator.resume_workers()

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
