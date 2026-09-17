"""Host and bench locks over BSD flock (stdlib), so fm processes stop being blind to each other.

One file, two grips: any number of SHARED holders coexist; an EXCLUSIVE holder is alone,
and only when nobody holds anything. Two flock properties carry the whole design: a grip
vanishes the instant its process exits or dies (kill -9 included -- there is no stale-lock
handling anywhere because there are no stale locks), and acquisition is non-blocking, so a
loser is refused instantly with a sentence instead of silently waiting.

Two tiers, same mechanism:

- ``locks/migration.lock`` (host): every ordinary command holds it SHARED ("don't migrate
  under me"), a migration holds it EXCLUSIVE ("I am rewriting fm's state"). Wired inline in
  ``app_callback`` (shared, AFTER the migration gate -- a process conflicts with its own
  grips, and the gate can launch the inline migration) and ``MigrationExecutor.execute``
  (exclusive, released in ``finally`` so the triggering command can then take its shared
  grip).
- ``locks/bench-<name>.lock`` (per bench): the bench mutators hold it EXCLUSIVE, a bench
  bake holds it SHARED (a long read the mutators must not rewrite from under). Applied with
  the :func:`bench_lock` decorator at each command's definition site. Quick readers (logs,
  info, shell) deliberately hold nothing.

The holder writes ``operation (pid N)`` into the file so a refusal can name the culprit.
With several SHARED holders the file carries the most recent one -- the LOCK is always
exact, only that diagnostic sentence is best-effort.
"""

import fcntl
import functools
import os
from pathlib import Path
from typing import IO

from frappe_manager import CLI_DIR

LOCKS_DIR: Path = CLI_DIR / "locks"
MIGRATION_LOCK_NAME = "migration.lock"


def migration_lock_path() -> Path:
    return LOCKS_DIR / MIGRATION_LOCK_NAME


def bench_lock_path(bench_name: str) -> Path:
    return LOCKS_DIR / f"bench-{bench_name}.lock"


def acquire(path: Path, exclusive: bool, holder: str | None = None) -> "IO[str] | None":
    """Non-blocking grip on ``path``; the open handle IS the grip.

    Returns the handle (keep it referenced; closing it -- or the process dying -- releases
    the grip) or ``None`` when a conflicting holder exists. ``holder`` is recorded in the
    file for :func:`read_holder`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    handle = path.open("r+")
    flags = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
    try:
        fcntl.flock(handle, flags)
    except OSError:
        handle.close()
        return None
    if holder is not None:
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{holder} (pid {os.getpid()})")
            handle.flush()
        except OSError:
            pass  # the grip is what matters; the name in the file is diagnostic only
    return handle


def read_holder(path: Path) -> str | None:
    """Best-effort name of who holds ``path`` (see the shared-holders caveat above)."""
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return text or None


def bench_lock(param: str = "benchname", shared: bool = False, operation: str | None = None):
    """Hold this bench's lock for the decorated command's whole run.

    ``param`` names the command argument carrying the bench (its typer callback has
    already resolved pickers, BENCH/SITE addresses and legacy names by the time the
    wrapper runs, so the value here is a plain bench name). ``None`` -- a standalone
    bake, nothing bench-scoped at all -- means there is nothing to lock: no bench, no
    bench lock.

    Mutators take the default EXCLUSIVE grip; a long reader like bake passes
    ``shared=True``. The grip is released when the command returns, raises, or dies.
    """

    def decorate(fn):
        op = operation or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bench_name = kwargs.get(param)
            handle = None
            if bench_name:
                path = bench_lock_path(str(bench_name))
                handle = acquire(path, exclusive=not shared, holder=op)
                if handle is None:
                    from frappe_manager.output_manager import get_global_output_handler

                    culprit = read_holder(path) or "another fm operation"
                    get_global_output_handler().exit(
                        f"Bench {bench_name} is busy: {culprit} is running on it. Let it finish, then re-run."
                    )
            try:
                return fn(*args, **kwargs)
            finally:
                if handle is not None:
                    handle.close()

        return wrapper

    return decorate
