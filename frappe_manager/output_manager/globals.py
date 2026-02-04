"""
Global OutputHandler singleton.

Ensures only one output handler exists at a time, preventing concurrent
spinner conflicts with the shared DisplayManager singleton.
"""

from typing import Optional

from frappe_manager.output_manager.base import OutputHandler

_global_output_handler: Optional[OutputHandler] = None


def set_global_output_handler(handler: Optional[OutputHandler]) -> None:
    """
    Set the global output handler.

    This replaces any existing handler. Should be called:
    1. Early in main.py (before app()) with basic handler
    2. In app_callback (after parsing) with upgraded handler
    3. In test cleanup with None to reset state

    Args:
        handler: The OutputHandler instance to use globally, or None to clear
    """
    global _global_output_handler
    _global_output_handler = handler


def get_global_output_handler() -> OutputHandler:
    """
    Get the global output handler.

    Returns:
        The global OutputHandler instance

    Raises:
        RuntimeError: If handler not initialized. This indicates a bug
                     in initialization order - handler should be set
                     in main.py:cli_entrypoint() before any commands run.
    """
    if _global_output_handler is None:
        raise RuntimeError(
            "Global output handler not initialized. "
            "This should be set in main.py:cli_entrypoint() before app() is called."
        )
    return _global_output_handler


def has_global_output_handler() -> bool:
    """
    Check if global handler is initialized.

    Returns:
        True if handler is set, False otherwise
    """
    return _global_output_handler is not None
