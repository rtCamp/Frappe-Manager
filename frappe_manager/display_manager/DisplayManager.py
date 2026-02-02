"""
DisplayManager module - REMOVED.

All functionality has been moved to RichOutputHandler.

For new code, use:
    from frappe_manager.output_manager import get_global_output_handler
    output = get_global_output_handler()

For legacy imports, richprint now directly maps to the global output handler.
"""


class DisplayManager:
    """
    REMOVED: Use get_global_output_handler() instead.

    This class is kept only to prevent import errors.
    It directly delegates to the global output handler.
    """

    def __init__(self):
        from frappe_manager.output_manager import get_global_output_handler

        self._handler = get_global_output_handler()

    def __getattr__(self, name):
        return getattr(self._handler, name)


_richprint_instance = None


def _get_richprint():
    """Get or create the richprint singleton (lazy initialization)."""
    from frappe_manager.output_manager import (
        get_global_output_handler,
        has_global_output_handler,
        set_global_output_handler,
    )
    from frappe_manager.output_manager.rich_output import RichOutputHandler

    global _richprint_instance
    if _richprint_instance is None:
        if has_global_output_handler():
            _richprint_instance = get_global_output_handler()
        else:
            _richprint_instance = RichOutputHandler()
            set_global_output_handler(_richprint_instance)

    return _richprint_instance


class _RichPrintProxy:
    """Proxy object that lazily initializes richprint on first access."""

    def __getattr__(self, name):
        return getattr(_get_richprint(), name)


richprint = _RichPrintProxy()
