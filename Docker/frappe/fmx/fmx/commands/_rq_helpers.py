import sys
import traceback
from fmx.rq_controller import (
    control_rq_workers,
    wait_for_rq_workers_suspended,
    is_rq_suspended,
    ActionEnum,
)
from fmx.display import DisplayManager


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
    display.print("⏸️  Suspending RQ workers...")
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
    if not is_rq_suspended():
        return True

    display.print("▶️  Resuming RQ workers...")
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
