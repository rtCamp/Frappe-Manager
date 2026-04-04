from typing import Annotated, List, Optional
import typer
from fmx.cli import (
    ServiceNameEnumFactory,
    execute_parallel_command,
    get_service_names_for_completion,
)
from fmx.command_utils import get_process_description, validate_services
from fmx.display import DisplayManager
from fmx.supervisor import stop_service as util_stop_service
from fmx.commands._rq_helpers import suspend_rq_workers, resume_rq_workers

command_name = "stop"

ServiceNamesEnum = ServiceNameEnumFactory()


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
            help="Target only specific process(es) within the selected service(s). Use multiple times for multiple processes (e.g., -p worker_short -p worker_long).",
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
            help="Timeout (seconds) for --drain-workers (default: 300).",
        ),
    ] = 300,
    drain_workers_poll: Annotated[
        int,
        typer.Option(
            "--drain-workers-poll",
            help="Polling interval (seconds) for --drain-workers (default: 5).",
        ),
    ] = 5,
    skip_stale_workers: Annotated[
        bool,
        typer.Option(
            "--skip-stale-workers/--no-skip-stale-workers",
            help="When used with --drain-workers, skip workers that remain idle after suspension is set. "
            "Workers idle longer than --skip-stale-timeout seconds are treated as dead and ignored.",
        ),
    ] = True,
    skip_stale_timeout: Annotated[
        int,
        typer.Option(
            "--skip-stale-timeout",
            help="Seconds after which an idle post-suspension worker is considered stale and skipped (default: 15). "
            "Used with --skip-stale-workers.",
        ),
    ] = 15,
    force_kill_timeout: Annotated[
        Optional[int],
        typer.Option(
            "--force-kill-timeout",
            help="Timeout (seconds) after which stubborn non-worker processes will be forcefully killed during stop.",
        ),
    ] = None,
):
    """Stop services or specific processes."""
    display: DisplayManager = ctx.obj['display']
    debug: bool = ctx.obj.get('debug', False)

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    valid, target_desc = validate_services(display, services_to_target, all_services, "stop")
    if not valid:
        return

    process_desc = get_process_description(display, process_name)
    wait_desc = "(with wait)" if wait else "(without wait)"
    display.print(f"\nAttempting to stop {process_desc} in {target_desc} {wait_desc}...")

    try:
        if drain_workers:
            if not suspend_rq_workers(
                display,
                drain_workers,
                drain_workers_timeout,
                drain_workers_poll,
                debug,
                skip_stale=skip_stale_workers,
                stale_timeout=skip_stale_timeout,
            ):
                raise typer.Exit(code=1)

        execute_parallel_command(
            services_to_target,
            util_stop_service,
            action_verb="stopping",
            show_progress=True,
            process_name_list=process_name,
            wait=wait,
            wait_workers=drain_workers,
            force_kill_timeout=force_kill_timeout,
        )

    finally:
        if drain_workers:
            resume_rq_workers(display)
        display.print("\nStop sequence complete.")
