from typing import Annotated, List, Optional
import typer

try:
    from typer_examples import example as _example
except ImportError:

    def _example(*a, **kw):  # type: ignore[misc]
        def _noop(fn):
            return fn

        return _noop


from fmx.cli import (
    ServiceNameEnumFactory,
    execute_parallel_command,
    get_service_names_for_completion,
)
from fmx.command_utils import get_process_description, validate_services, resolve_service_targets, format_wait_desc
from fmx.display import DisplayManager
from fmx.supervisor import stop_service as util_stop_service
from fmx.commands._rq_helpers import suspend_rq_workers, resume_rq_workers, run_with_optional_rq_drain

command_name = "stop"

ServiceNamesEnum = ServiceNameEnumFactory()


@_example(
    "Stop all services immediately",
    "",
    detail=(
        "Stops every supervisor service. Workers are killed via SIGUSR1 — any job currently "
        "executing is interrupted and may be marked failed or retried. Use when you need a "
        "fast, clean shutdown and losing an in-flight background job is acceptable."
    ),
)
@_example(
    "Stop a specific service only",
    "{svc}",
    svc="short-worker",
    detail=(
        "Target a single service by name instead of stopping everything. Useful for "
        "replacing a worker binary or pausing a queue without affecting web, socketio, or "
        "the scheduler. Service names: frappe, short-worker, long-worker, schedule, socketio."
    ),
)
@_example(
    "Stop a specific process inside a service",
    "{svc} -p {proc}",
    svc="long-worker",
    proc="long-worker_1",
    detail=(
        "Use -p when a service runs multiple process instances and only one needs to be "
        "stopped — e.g. to reduce concurrency without a full service shutdown."
    ),
)
@_example(
    "Drain workers before stopping",
    "--drain-workers",
    detail=(
        "Sets a Redis suspend flag so workers stop picking up new jobs, then waits up to "
        "5 minutes for every worker to become idle before stopping the services. Use when "
        "workers are executing jobs you cannot afford to lose — e.g. email sends, file "
        "imports, or report generation."
    ),
)
@_example(
    "Drain with custom timeout and poll interval",
    "--drain-workers --drain-workers-timeout 600 --drain-workers-poll 10",
    detail=(
        "Increase the timeout for benches where long-running jobs are expected to take more "
        "than the default 5 minutes. Increasing --drain-workers-poll reduces supervisor "
        "polling noise on busy systems."
    ),
)
@_example(
    "Tune the force-kill window for slow workers",
    "--worker-kill-timeout 30 --worker-kill-poll 2",
    detail=(
        "After SIGUSR1, fmx polls every --worker-kill-poll seconds and waits up to "
        "--worker-kill-timeout seconds for the process to exit before escalating to "
        "supervisord stopProcess. Increase when workers need extra time to finish their "
        "current loop before honouring the shutdown signal. Only applies to the non-drain path."
    ),
)
def command(
    ctx: typer.Context,
    service_names: Annotated[
        Optional[List[ServiceNamesEnum]],
        typer.Argument(
            help="Name(s) of the service(s) to target. If omitted, targets ALL running services.",
            autocompletion=get_service_names_for_completion,
            show_default=False,
        ),
    ] = None,
    process_name: Annotated[
        Optional[List[str]],
        typer.Option(
            "--process",
            "-p",
            help="Target only specific process(es) within the selected service(s). If omitted, stops ALL processes in the service. Use multiple times to target multiple processes.",
            show_default=False,
        ),
    ] = None,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Wait for supervisor stop operations to complete before returning.",
        ),
    ] = True,
    drain_workers: Annotated[
        bool,
        typer.Option(
            "--drain-workers",
            help="Suspend RQ workers via Redis flag and wait for them to finish their current jobs before stopping.",
            is_flag=True,
        ),
    ] = False,
    drain_workers_timeout: Annotated[
        int,
        typer.Option(
            "--drain-workers-timeout",
            help="Timeout in seconds to wait for workers to drain. Set to 0 (default) to wait indefinitely.",
        ),
    ] = 0,
    drain_workers_poll: Annotated[
        int,
        typer.Option(
            "--drain-workers-poll",
            help="Polling interval in seconds when waiting for workers to drain.",
        ),
    ] = 5,
    skip_stale_workers: Annotated[
        bool,
        typer.Option(
            "--skip-stale-workers/--no-skip-stale-workers",
            help="With --drain-workers, treat workers idle for more than --skip-stale-timeout seconds as dead and skip them.",
        ),
    ] = True,
    skip_stale_timeout: Annotated[
        int,
        typer.Option(
            "--skip-stale-timeout",
            help="Seconds after which an idle post-suspension worker is treated as dead and skipped. Used with --skip-stale-workers.",
        ),
    ] = 15,
    worker_kill_timeout: Annotated[
        int,
        typer.Option(
            "--worker-kill-timeout",
            help="Timeout in seconds to wait for a worker process to exit after SIGUSR1 before falling back to stopProcess.",
        ),
    ] = 15,
    worker_kill_poll: Annotated[
        float,
        typer.Option(
            "--worker-kill-poll",
            help="Polling interval in seconds when waiting for a worker to exit after SIGUSR1.",
        ),
    ] = 3.0,
):
    """Stop services or specific processes."""
    display: DisplayManager = ctx.obj['display']
    debug: bool = ctx.obj.get('debug', False)

    all_services = get_service_names_for_completion()
    services_to_target = resolve_service_targets(service_names, all_services)

    valid, target_desc = validate_services(display, services_to_target, all_services, "stop")
    if not valid:
        return

    process_desc = get_process_description(display, process_name)
    wait_desc = format_wait_desc(wait)
    display.print(f"\nStopping {process_desc} in {target_desc} {wait_desc}...")

    def _do_stop():
        execute_parallel_command(
            services_to_target,
            util_stop_service,
            action_verb="stopping",
            show_progress=True,
            process_name_list=process_name,
            wait=wait,
            wait_workers=drain_workers,
            worker_kill_timeout=worker_kill_timeout,
            worker_kill_poll=worker_kill_poll,
        )

    run_with_optional_rq_drain(
        display=display,
        drain_workers=drain_workers,
        drain_workers_timeout=drain_workers_timeout,
        drain_workers_poll=drain_workers_poll,
        debug=debug,
        skip_stale=skip_stale_workers,
        stale_timeout=skip_stale_timeout,
        action_fn=_do_stop,
    )
