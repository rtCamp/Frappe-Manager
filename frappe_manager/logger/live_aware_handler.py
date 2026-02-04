"""
Live-aware logging handlers.

Handlers that coordinate with Rich Live display to prevent output corruption.
"""

import logging
import threading
from typing import Any

from rich.logging import RichHandler


class LiveAwareRichHandler(RichHandler):
    """
    RichHandler that coordinates with Rich Live spinner display.

    When the Live display (spinner) is active, logger output can corrupt the
    display and create visual artifacts. This handler temporarily stops the
    Live display during emit(), then restarts it.

    Usage:
        from frappe_manager.output_manager import get_global_output_handler

        output = get_global_output_handler()
        handler = LiveAwareRichHandler(
            console=output.stderr,
            live_display=output.live,
            output_lock=output._lock,
        )
        logger.addHandler(handler)
    """

    def __init__(self, *args, live_display=None, output_lock=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._live_display = live_display
        self._output_lock = output_lock or threading.RLock()

    def emit(self, record: logging.LogRecord) -> None:
        if self._live_display and self._live_display.is_started:
            with self._output_lock:
                self._live_display.stop()
                try:
                    super().emit(record)
                finally:
                    self._live_display.start(refresh=True)
        else:
            super().emit(record)
