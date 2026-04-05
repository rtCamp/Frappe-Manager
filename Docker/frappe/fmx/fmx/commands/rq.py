import typer
from typing import Annotated
from fmx.display import DisplayManager

try:
    from typer_examples import example as _example
except ImportError:

    def _example(*a, **kw):  # type: ignore[misc]
        def _noop(fn):
            return fn

        return _noop


from fmx.rq_controller import (
    control_rq_workers,
    get_rq_worker_status,
    is_rq_suspended,
    ActionEnum,
)
from fmx.supervisor.status_formatter import format_rq_status
from fmx.commands._rq_helpers import MSG_SUSPENDING, MSG_RESUMING

command_name = "rq"

rq_app = typer.Typer(help="Manage RQ workers (suspend, resume, status)")


@rq_app.command("suspend")
@_example(
    "Suspend workers before manual DB work",
    "",
    detail=(
        "Sets a Redis flag that tells all RQ workers to stop dequeuing new jobs. Workers "
        "currently executing a job will finish it first — they just won't pick up the next "
        "one. Use before running a manual SQL migration, patching a custom app, or any "
        "operation where a background job touching the DB could cause data corruption."
    ),
)
def suspend_command(
    ctx: typer.Context,
):
    """Suspend RQ workers to prevent them from picking up new jobs."""
    display: DisplayManager = ctx.obj['display']

    if is_rq_suspended():
        display.warning("⚠ RQ workers are already suspended")
        return

    display.print(MSG_SUSPENDING)
    success = control_rq_workers(action=ActionEnum.suspend)

    if success:
        display.print("✔ RQ workers suspended successfully")
    else:
        display.error("✗ Failed to suspend RQ workers")
        raise typer.Exit(code=1)


@rq_app.command("resume")
@_example(
    "Resume workers after maintenance",
    "",
    detail=(
        "Clears the Redis suspend flag so workers start picking up jobs again. Run this "
        "after you have finished manual DB work, applied a patch, or completed any "
        "maintenance window that required workers to be idle. Workers resume immediately — "
        "queued jobs start processing within seconds."
    ),
)
def resume_command(
    ctx: typer.Context,
):
    """Resume RQ workers to allow them to pick up new jobs."""
    display: DisplayManager = ctx.obj['display']

    if not is_rq_suspended():
        display.warning("⚠ RQ workers are already active (not suspended)")
        return

    display.print(MSG_RESUMING)
    success = control_rq_workers(action=ActionEnum.resume)

    if success:
        display.print("✔ RQ workers resumed successfully")
    else:
        display.error("✗ Failed to resume RQ workers")
        raise typer.Exit(code=1)


@rq_app.command("status")
@_example(
    "Check suspend state and queue depths",
    "",
    detail=(
        "Shows whether workers are suspended, how many jobs are queued in each queue "
        "(default, short, long), and which workers are registered. Use after a 'rq suspend' "
        "or 'stop --drain-workers' to confirm workers are idle before proceeding with "
        "maintenance — or after 'rq resume' to confirm they are active again."
    ),
)
@_example(
    "Full worker detail — active jobs and queue assignments",
    "--verbose",
    detail=(
        "Expands each worker row to show its assigned queues, current job ID, and job "
        "description. Use when you need to identify a stuck worker, see what a specific "
        "queue is processing right now, or debug an unexpected queue backlog."
    ),
)
def status_command(
    ctx: typer.Context,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show detailed worker information.",
        ),
    ] = False,
):
    """Show RQ worker status."""
    display: DisplayManager = ctx.obj['display']

    display.print("Fetching RQ worker status...")

    rq_status = get_rq_worker_status(include_dead=False)

    if not rq_status:
        display.error("✗ Could not fetch RQ worker status")
        raise typer.Exit(code=1)

    rq_tree = format_rq_status(rq_status, verbose=verbose)
    if rq_tree:
        display.print(rq_tree)
    else:
        display.warning("⚠ No RQ workers found")


command = rq_app
