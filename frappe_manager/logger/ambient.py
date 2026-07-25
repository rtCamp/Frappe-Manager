"""
Ambient logging context.

One `ContextVar` carries the invocation's LoggerContext (correlation id, bench,
operation, ...). Context is set at a few chokepoints (CLI entry, bench load,
migration/ssl scopes) and stamped onto EVERY log record at emit time by
``ContextInjectFilter`` (see log.py) -- no logger objects are threaded through
constructors.

Usage:
    set_context(correlation_id=uuid, operation="create")   # non-scoped merge
    with bind(operation="migrate-0.19.0"):                 # scoped push/reset
        ...
    ctx_submit(executor, fn, *args)                         # thread propagation
"""

import contextvars
from collections.abc import Callable, Generator
from concurrent.futures import Executor, Future
from contextlib import contextmanager

from frappe_manager.logger.context import LoggerContext

# Treated as immutable everywhere (child() copies); safe to share as the default.
_EMPTY_CONTEXT = LoggerContext()

_context: contextvars.ContextVar[LoggerContext] = contextvars.ContextVar("fm_log_context")


def current_context() -> LoggerContext:
    """The ambient LoggerContext for this thread/task."""
    return _context.get(_EMPTY_CONTEXT)


def set_context(**overrides) -> None:
    """Merge ``overrides`` into the ambient context, non-scoped.

    For invocation-lifetime facts: correlation id at CLI entry, bench name at
    bench load. Later calls override earlier values (e.g. per-bench loops).
    """
    _context.set(current_context().child(**overrides))


def reset_context() -> None:
    """Clear the ambient context (test isolation)."""
    _context.set(LoggerContext())


@contextmanager
def bind(**overrides) -> Generator[LoggerContext]:
    """Scoped context: merge ``overrides``, restore the previous context on exit."""
    token = _context.set(current_context().child(**overrides))
    try:
        yield _context.get()
    finally:
        _context.reset(token)


def ctx_submit(executor: Executor, fn: Callable, /, *args, **kwargs) -> Future:
    """``executor.submit`` that propagates the caller's ambient context.

    contextvars do not cross thread boundaries by themselves; worker-thread log
    records would lose corr/bench/op tags without this.
    """
    return executor.submit(contextvars.copy_context().run, fn, *args, **kwargs)
