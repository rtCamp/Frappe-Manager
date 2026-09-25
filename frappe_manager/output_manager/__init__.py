"""
Output abstraction layer for Frappe Manager.

This module provides an abstract interface for handling output in business logic,
allowing the CLI to be decoupled from the core modules. This enables future support
for alternative interfaces (API, WebSocket, etc.) without rewriting business logic.
"""

import os

from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.output_manager.context_managers import (
    nested_spinner,
    spinner,
    spinner_or_pass,
    temporary_stop,
)
from frappe_manager.output_manager.globals import (
    get_global_output_handler,
    has_global_output_handler,
    set_global_output_handler,
)
from frappe_manager.output_manager.json_output import JSONOutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.silent_output import SilentOutputHandler

__all__ = [
    "JSONOutputHandler",
    "OutputHandler",
    "RichOutputHandler",
    "SilentOutputHandler",
    "get_global_output_handler",
    "has_global_output_handler",
    "nested_spinner",
    "set_global_output_handler",
    "spinner",
    "spinner_or_pass",
    "temporary_stop",
    "warn_or_log",
]


def warn_or_log(component: str, message: str) -> None:
    """Warn through the interactive output handler when one is attached, else the named
    component's own logger.

    Shared by the config readers (`BenchConfig.import_from_toml`, `FMConfigManager.import_from_toml`)
    for an unrecognised key: those run inside `fm list`/`fm bake`/`fm switch`/`fm maintenance`,
    which skip the migration gate, so one bench with a stale or misspelled key must surface a
    warning rather than take down a command every bench on the host shares.

    Silent during shell completion, checked FIRST and unconditionally: `cli_entrypoint()`
    installs a `RichOutputHandler` (main.py) before `app()` runs, and completion dispatches
    from inside `app()` (`click.core.Command._main_shell_completion` /
    `typer.core._typer_main_shell_completion`, both called from `Command.main()`), so a handler
    IS attached for that whole codepath -- "no handler attached" was never what kept completion
    quiet. Both of those gate on the same env var they set before invoking: `_{PROG_NAME}_COMPLETE`
    (`_FM_COMPLETE` here, fm's console-script name), non-empty for exactly as long as a shell is
    asking for completions, for every shell click supports. `_TYPER_COMPLETE_ARGS` is a second,
    narrower variable typer's own completion classes read for the words being completed, not a
    completion-in-progress signal, and click's bash completion script never sets it at all -- so
    `_FM_COMPLETE` is the one check that is actually reliable here.

    Never raises: the logger fallback writes to the rotating file handler only (no console
    handler unless one was explicitly configured). That protects a genuinely different case from
    the one above: a script or test that imports `BenchConfig`/`FMConfigManager` and calls
    `import_from_toml` directly, without ever going through `cli_entrypoint()`, where
    `has_global_output_handler()` is false because nothing has set one yet.
    """
    if os.environ.get("_FM_COMPLETE"):
        return
    if has_global_output_handler():
        get_global_output_handler().warning(message)
    else:
        from frappe_manager.logger import get_logger

        get_logger(component=component).warning(message)
