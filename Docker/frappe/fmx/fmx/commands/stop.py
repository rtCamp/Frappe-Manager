from typing import Annotated, List, Optional
import typer
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
            help="Timeout in seconds to wait for workers to drain.",
        ),
    ] = 300,
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
    display.print(f"\nAttempting to stop {process_desc} in {target_desc} {wait_desc}...")

    def _do_stop():
        execute_parallel_command(
            services_to_target,
            util_stop_service,
            action_verb="stopping",
            show_progress=True,
            process_name_list=process_name,
            wait=wait,
            wait_workers=drain_workers,
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
        completion_message="\nStop sequence complete.",
    )
