from . import log
from .ambient import bind, ctx_submit, current_context, reset_context, set_context
from .context import LoggerContext
from .log import FMLogger


def get_logger(component: str | None = None) -> FMLogger:
    """The one logger acquisition pattern.

    Returns a context-aware adapter over the singleton "fm" logger. ``component``
    names the caller (static); bench / operation / correlation id are ambient
    (set via :func:`set_context` / :func:`bind`) and stamped at emit time.
    """
    return FMLogger(log.get_logger(), component=component)


__all__ = [
    "FMLogger",
    "LoggerContext",
    "bind",
    "ctx_submit",
    "current_context",
    "get_logger",
    "log",
    "reset_context",
    "set_context",
]
