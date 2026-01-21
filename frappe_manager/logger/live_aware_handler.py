"""
Live-aware logging handlers.

Handlers that coordinate with Rich Live display to prevent output corruption.
"""

import logging
from typing import Any

from rich.logging import RichHandler


class LiveAwareRichHandler(RichHandler):
    """
    RichHandler that coordinates with Rich Live spinner display.

    When the Live display (spinner) is active, logger output can corrupt the
    display and create visual artifacts. This handler temporarily stops the
    Live display during emit(), then restarts it.

    Usage:
        from frappe_manager.display_manager.DisplayManager import richprint

        handler = LiveAwareRichHandler(
            console=richprint.stderr,
            live_display=richprint.live,
        )
        logger.addHandler(handler)
    """

    def __init__(self, *args, live_display=None, **kwargs):
        """
        Initialize LiveAwareRichHandler.

        Args:
            *args: Positional arguments for RichHandler
            live_display: Rich Live instance to coordinate with (optional)
            **kwargs: Keyword arguments for RichHandler
        """
        super().__init__(*args, **kwargs)
        self._live_display = live_display

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a record, temporarily stopping Live display if active.

        Args:
            record: The log record to emit
        """
        if self._live_display and self._live_display.is_started:
            self._live_display.stop()
            try:
                super().emit(record)
            finally:
                self._live_display.start(refresh=True)
        else:
            super().emit(record)
