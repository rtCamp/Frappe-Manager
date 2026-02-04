"""
Live-aware logging handlers.

Handlers that coordinate with Rich Live display to prevent output corruption.
"""

import logging
from typing import Any

from rich.logging import RichHandler


class LiveAwareRichHandler(RichHandler):
    """
    RichHandler that uses the same Console as the Live display.

    Rich Console automatically handles coordination between Live displays
    and regular print() calls, so no manual stop/start is needed.

    Usage:
        from frappe_manager.output_manager import get_global_output_handler

        output = get_global_output_handler()
        handler = LiveAwareRichHandler(
            console=output.stderr,
        )
        logger.addHandler(handler)
    """

    def __init__(self, *args, live_display=None, output_lock=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._live_display = live_display
        self._output_lock = output_lock
