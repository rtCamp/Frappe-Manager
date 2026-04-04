import typer
from typing import Annotated
from fmx.display import DisplayManager
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
