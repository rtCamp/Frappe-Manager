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
