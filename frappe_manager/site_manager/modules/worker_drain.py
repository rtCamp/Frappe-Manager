"""The one drain gate, shared by every command that disturbs RQ workers.

`fm restart`, `fm apps add`, `fm update` and `fm stop` all suspend the workers, wait for in-flight
jobs, and refuse rather than kill a job that overruns. That decision was written out by hand four
times, once per command, and the wording had already begun to drift between copies -- which
matters, because the timeout message is the one that tells an operator their work was NOT lost and
how to proceed.

Why it lives beside the orchestrator rather than under `commands/`: a command module importing
another command module is the wrong dependency direction, and it is exactly why `fm stop` grew its
own copy instead of reusing `fm update`'s.

The gate carries no policy switch. Every caller treats a timeout the same way -- nothing happens,
and `--no-drain` is the documented way through -- so "what to do on timeout" is not a parameter.
What callers DO differ on is who clears the suspend flag afterwards, which is why this returns
whether the caller owes a resume rather than resuming itself:

* `restart` / `apps add` / `update` resume after their own work completes;
* `stop` must resume BEFORE the containers go down, because `rq:suspended` is a redis key and the
  bench's redis-queue persists it (RDB `save` on a `/data` volume) -- a flag left set survives the
  stop and comes back with the bench, so `fm start` would bring up workers that process nothing.
"""

import contextlib
import os
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import typer

from frappe_manager.site_manager.modules.deploy_orchestrator import DrainUnavailable


def drain_gate(orchestrator, output, *, action: str) -> bool:
    """Suspend RQ and wait for in-flight jobs, or abort having changed nothing.

    Args:
        orchestrator: a ``DeployOrchestrator`` for the bench being acted on.
        output: the global output handler.
        action: what the caller was about to do, for the refusal message ("restart", "stop",
            "update", "install apps"). Only the verb differs between callers; the guarantee the
            message makes -- that nothing was changed -- does not.

    Returns:
        True when the workers really were suspended, i.e. when the caller owes them a resume.
        False when there was nothing to suspend, or when the image cannot be drained at all.

    Raises:
        typer.Exit: the drain ran and timed out with workers still busy. The workers are resumed
            first, so the bench is left exactly as it was found.
    """
    try:
        drained = orchestrator.drain_workers()
    except DrainUnavailable as e:
        # Not a timeout: an image predating fmx can never be drained, and no drain_timeout can fix
        # that. Warned about and carried on, rather than making the command impossible.
        output.warning(f"{e} Continuing without a drain: in-flight jobs may be interrupted.")
        return False

    if drained:
        return True

    # Resumed BEFORE the refusal: an abort that leaves the queue suspended would turn a refusal
    # into a silent outage, since the flag outlives this process.
    orchestrator.resume_workers()
    output.display_error(
        f"Drain timed out after {orchestrator.workers_config.drain_timeout}s: workers still busy. "
        f"Nothing was changed and the workers are still running, so the {action} can be retried. "
        "Raise \\[workers].drain_timeout, wait for the jobs to finish, or re-run with --no-drain "
        "to interrupt them."
    )
    raise typer.Exit(1)


@contextmanager
def _signal_scoped(output, undo: Callable[[], None], what: str) -> Iterator[None]:
    """Run ``undo`` if the block is left for ANY reason, signals included, then re-raise.

    SCOPED, never process-wide, and the difference matters. A global signal-to-exception handler
    would make every long destructive command unwind instead of dying, and two of those unwinds
    are worse than death: `fm migrate` defaults to `--on-failure=prompt`, which would prompt into
    the terminal SIGHUP has just taken away, and `fm switch` would begin a rollback measured in
    minutes. SIGTERM is normally followed by SIGKILL after a grace period (docker stop gives ten
    seconds), so a long unwind is truncated -- a partially rolled-back bench is worse than a
    merely half-finished one. The undos here are a single container exec each, fast enough to
    finish inside any grace period.

    Without this, the state a wait holds simply leaks: fm installs no SIGINT/SIGTERM/SIGHUP
    handlers (`main.py` touches only SIGPIPE), `atexit` does not run on signal death, so neither
    `finally` nor the registered cleanup gets a turn. Locks are the exception and need no help --
    they are BSD `flock`, which the kernel releases when the process dies.
    """
    fired = False

    def handler(signum, _frame):
        nonlocal fired
        if fired:
            # A second signal is an escape hatch, not a second cleanup: someone is pressing
            # Ctrl-C again because the undo itself is stuck. Restore the default disposition and
            # deliver the signal to ourselves, so it kills us the way it would have originally.
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        fired = True
        # KeyboardInterrupt, not a bespoke class: it is what Ctrl-C already raises, so SIGTERM and
        # SIGHUP take the identical path through every caller, and it is a BaseException -- fm's
        # `except Exception` arms must not swallow an operator's interrupt as an unexpected error.
        raise KeyboardInterrupt(f"signal {signum}")

    previous: dict = {}
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        # ValueError off the main thread, OSError for a signal the platform lacks. A no-op install
        # is survivable (the `finally` paths still run for exceptions); a crash here would not be.
        with contextlib.suppress(ValueError, OSError):
            previous[sig] = signal.signal(sig, handler)

    try:
        yield
    except BaseException:
        output.warning(f"Interrupted: {what}")
        undo()
        raise
    finally:
        for sig, old in previous.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, old)


@contextmanager
def frappe_maintenance_mode(orchestrator, output) -> Iterator[None]:
    """Hold frappe's own ``maintenance_mode`` for the block, and always clear it.

    Named for the mechanism, not the effect, because fm has a second unrelated thing called
    maintenance: `fm maintenance` writes an nginx 503 block and does NOT touch this flag. This one
    is `bench set-config maintenance_mode`, which also makes `is_scheduler_inactive` true
    (frappe/utils/scheduler.py) -- that is why it stops the scheduler enqueuing, and why it is the
    lever for emptying a queue rather than merely freezing it.

    `pause_scheduler` is set with it, and cleared with it. See `pause` below for why both.
    """
    def pause(value: int) -> None:
        # BOTH keys, always together. `maintenance_mode` alone already makes
        # `is_scheduler_inactive` true, but `pause_scheduler` is the one that names what fm
        # actually wants (the scheduler stops enqueuing); the 503 is a side effect it tolerates.
        # Setting both also survives an operator clearing one by hand mid-operation, which would
        # otherwise silently let the scheduler refill a queue fm is trying to empty.
        orchestrator.set_maintenance_mode(value)
        orchestrator.set_scheduler_paused(value)

    pause(1)
    with _signal_scoped(output, lambda: pause(0), "resuming producers"):
        yield
    pause(0)


@contextmanager
def rq_suspended(orchestrator, output, *, action: str) -> Iterator[None]:
    """Hold RQ's suspend flag for the block, and always clear it if we set it.

    Named after the flag itself (`rq:suspended`, a redis key) rather than a vague worker state,
    because the key outliving the process is the whole hazard: workers alive and consuming
    nothing is a silent outage, and it survives a restart of the bench.

    Replaces the hand-written `drained = drain_gate(...)` plus `try/finally: resume` pair each
    caller carried, so a new caller cannot forget the resume.
    """
    drained = drain_gate(orchestrator, output, action=action)

    def undo() -> None:
        if drained:
            orchestrator.resume_workers()

    with _signal_scoped(output, undo, "resuming RQ workers"):
        yield
    undo()
