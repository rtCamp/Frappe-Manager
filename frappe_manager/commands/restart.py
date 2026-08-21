from typing import Annotated

import typer
from click.core import ParameterSource
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.bench_config import WorkersConfig
from frappe_manager.site_manager.site import Bench

# Rich help panels for `fm restart --help`, grouped by concern.
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
    container: Annotated[
        bool,
        typer.Option(
            "--container",
            help="Restart whole containers instead of supervisor processes: slower, and it starts a stopped bench.",
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
            help="Zero-downtime recreate of the web tier on the current image tag; image benches only.",
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

    Workers drain first: fm waits for in-flight jobs and aborts the restart rather than kill a job that does not finish in time. --no-drain skips the wait and interrupts running jobs; --force kills everything fast.

    Supervisor restarts need a running bench. For a stopped one use fm start, or --container to restart-and-start the containers.
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

    from frappe_manager.site_manager.modules.deploy_orchestrator import (
        DeployError,
        DeployOrchestrator,
        DrainUnavailable,
    )

    orchestrator = DeployOrchestrator(bench, output_handler=output)

    # Supervisor-level restart execs into running containers; a stopped bench
    # needs start (or --container, which starts containers as it restarts them).
    if not container and not bench.running:
        output.error(
            f"Bench '{benchname}' is not fully running; use 'fm start {benchname}' "
            f"(or 'fm restart --container' to restart-and-start containers).",
            exception=typer.Exit(code=1),
        )

    def _drain_gate() -> bool:
        """Drain is a GATE: suspend workers and wait for in-flight jobs; on
        timeout resume the workers and abort the restart before any leg runs.

        The RQ suspend flag lives in redis, so workers restarted mid-drain come
        back suspended until resume -- ordering is safe even across the restart.

        Returns True when the workers really were suspended, i.e. when the caller
        owes them a resume. An image with no fmx cannot be drained at all: that
        is warned about and the restart carries on undrained (the same fallback
        the worker cycle already makes for supervisorctl), because it is not a
        timeout and no drain_timeout can fix it.
        """
        try:
            drained = orchestrator.drain_workers()
        except DrainUnavailable as e:
            output.warning(f"{e} Restarting without a drain: in-flight jobs may be interrupted.")
            return False
        if drained:
            return True
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
                        bench.restart_supervisor_service(
                            svc, docker_client_obj=bench.workers.docker_client, force=force
                        )
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
            drained = _drain_gate()
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
            drained = _drain_gate()
        try:
            if web:
                bench.restart_web_containers_services(use_container_restart=use_container_restart, force=force)

            # Inside the drained window, before the workers come back: a redis
            # bounce after resume_workers() kills the jobs the just-resumed
            # workers picked up, voiding the guarantee the drain paid for.
            if redis:
                bench.restart_redis_services_containers()

            if workers:
                _restart_workers(use_container_restart=use_container_restart)
        finally:
            if drained:
                orchestrator.resume_workers()

        if nginx:
            bench.restart_nginx_service(force=force)

        # A restart that leaves the site dead must not exit 0.
        if web:
            try:
                bench.orchestrator.verify_bench_server_responding()
            except Exception as e:
                output.display_error(f"Restart completed but the bench server is not responding: {e}")
                raise typer.Exit(1) from e
