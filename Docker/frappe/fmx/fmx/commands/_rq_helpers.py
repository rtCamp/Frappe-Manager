import sys
import traceback
from typing import Callable, Optional
from fmx.rq_controller import (
    control_rq_workers,
    wait_for_rq_workers_suspended,
    is_rq_suspended,
    ActionEnum,
)
from fmx.display import DisplayManager

MSG_SUSPENDING = "⏸️  Suspending RQ workers..."
MSG_RESUMING = "▶️  Resuming RQ workers..."


def suspend_rq_workers(
    display: DisplayManager,
    drain_workers: bool,
    drain_workers_timeout: int,
    drain_workers_poll: int,
    debug: bool = False,
    skip_stale: bool = True,
    stale_timeout: int = 15,
) -> bool:
    """Set the rq:suspended flag in Redis and optionally wait for all workers to reach suspended state.

    Returns False if suspension fails or workers don't drain within timeout.
    """
    display.print(MSG_SUSPENDING)
    try:
        success = control_rq_workers(action=ActionEnum.suspend)

        if not success:
            display.error("Failed to suspend RQ workers.")
            display.print("Aborting.")
            return False

        suspension_status = is_rq_suspended()

        if suspension_status is not True:
            display.error("Failed to verify RQ suspension flag in Redis.")
            display.print("Aborting.")
            return False

        if drain_workers:
            display.print("\nWaiting for RQ workers to complete their current jobs...")
            if not wait_for_rq_workers_suspended(
                timeout=drain_workers_timeout,
                poll_interval=drain_workers_poll,
                skip_stale=skip_stale,
                stale_timeout=stale_timeout,
            ):
                display.error("Workers did not become idle within the timeout period.")
                display.print("Aborting to avoid interrupting jobs.")
                control_rq_workers(action=ActionEnum.resume)
                return False

    except Exception as e:
        display.error(f"An unexpected error occurred during worker suspension or verification: {e}")
        if debug:
            traceback.print_exc()
        return False

    return True


def resume_rq_workers(display: DisplayManager) -> bool:
    """Resume RQ workers by clearing the rq:suspended flag in Redis.

    Returns True if workers were already resumed or successfully resumed.
    Returns False if the resume call fails.
    """
    if not is_rq_suspended():
        return True

    display.print(MSG_RESUMING)
    try:
        success = control_rq_workers(action=ActionEnum.resume)

        if not success:
            display.warning("Failed to resume RQ workers. Workers may remain suspended.")
            return False

    except Exception as e:
        display.error(f"Error resuming workers: {e}")
        traceback.print_exc(file=sys.stderr)
        return False

    return True


def run_with_optional_rq_drain(
    display: DisplayManager,
    drain_workers: bool,
    drain_workers_timeout: int,
    drain_workers_poll: int,
    debug: bool,
    skip_stale: bool,
    stale_timeout: int,
    action_fn: Callable[[], None],
    pre_action_fn: Optional[Callable[[], None]] = None,
    completion_message: str = "",
) -> None:
    """Wrap an action with optional RQ worker suspension and guaranteed resume.

    Suspend RQ workers (if drain_workers is True), optionally run a pre-action
    hook, then call action_fn.  Workers are always resumed in the finally block
    regardless of whether the action raises.

    Args:
        display: DisplayManager for output.
        drain_workers: When True, suspend workers and wait for drain before
            calling action_fn.
        drain_workers_timeout: Timeout (seconds) for the drain wait.
        drain_workers_poll: Polling interval (seconds) for the drain wait.
        debug: Show full tracebacks on unexpected errors.
        skip_stale: Treat workers idle longer than stale_timeout as dead.
        stale_timeout: Seconds after which an idle worker is considered stale.
        action_fn: Callable executed after optional suspension (and pre_action_fn).
            Should raise on failure.
        pre_action_fn: Optional callable executed after suspension but before
            action_fn.  Useful for steps that must happen between drain and the
            main action (e.g. disabling maintenance mode).
        completion_message: If non-empty, printed after the finally block.

    Raises:
        typer.Exit: If suspension fails (code 1).
        Any exception raised by action_fn or pre_action_fn is re-raised.
    """
    import typer

    try:
        if drain_workers:
            if not suspend_rq_workers(
                display,
                drain_workers,
                drain_workers_timeout,
                drain_workers_poll,
                debug,
                skip_stale=skip_stale,
                stale_timeout=stale_timeout,
            ):
                raise typer.Exit(code=1)

        if pre_action_fn is not None:
            pre_action_fn()

        action_fn()

    finally:
        if drain_workers:
            resume_rq_workers(display)
        if completion_message:
            display.print(completion_message)
