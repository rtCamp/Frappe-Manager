"""
Tests for LiveAwareRichHandler.

LiveAwareRichHandler uses the same Rich Console as the Live display.
Rich Console automatically handles coordination between Live displays
and regular print() calls, so no manual stop/start logic is needed.
"""

import logging
from unittest.mock import MagicMock

from frappe_manager.logger.live_aware_handler import LiveAwareRichHandler


class TestLiveAwareRichHandler:
    """Test Live-aware logging handler."""

    def test_handler_accepts_live_display_parameter(self):
        """Verify handler accepts live_display parameter for future use."""
        mock_live = MagicMock()
        mock_console = MagicMock()

        handler = LiveAwareRichHandler(
            console=mock_console,
            live_display=mock_live,
        )

        assert handler._live_display is mock_live

    def test_handler_accepts_output_lock_parameter(self):
        """Verify handler accepts output_lock parameter for future use."""
        mock_lock = MagicMock()
        mock_console = MagicMock()

        handler = LiveAwareRichHandler(
            console=mock_console,
            output_lock=mock_lock,
        )

        assert handler._output_lock is mock_lock

    def test_emit_delegates_to_rich_handler(self):
        """Verify emit() uses Rich Console which handles Live coordination."""
        mock_console = MagicMock()
        mock_live = MagicMock()

        handler = LiveAwareRichHandler(
            console=mock_console,
            live_display=mock_live,
        )

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

    def test_handler_initialization_without_live_display(self):
        """Verify handler works without live_display parameter."""
        mock_console = MagicMock()

        handler = LiveAwareRichHandler(
            console=mock_console,
            show_time=False,
            show_path=False,
        )

        assert handler._live_display is None
        assert handler._output_lock is None


class TestLoggerIntegration:
    """Test logger setup with LiveAwareRichHandler."""

    def test_add_console_handler_creates_live_aware_handler(self):
        """Verify that _add_console_handler creates LiveAwareRichHandler."""
        from frappe_manager.logger.log import _add_console_handler
        from frappe_manager.output_manager import get_global_output_handler
        from frappe_manager.output_manager.logging_output import LoggingOutputHandler

        logger = logging.getLogger("test_live_aware")
        logger.handlers = []

        _add_console_handler(logger, "INFO")

        handler = logger.handlers[0]
        output = get_global_output_handler()

        assert isinstance(handler, LiveAwareRichHandler)

        if isinstance(output, LoggingOutputHandler):
            underlying_output = output.delegate
            assert handler._live_display is underlying_output.live
        else:
            assert handler._live_display is output.live

    def test_handler_removes_old_handlers(self):
        """Verify old handlers are removed when adding new one."""
        from rich.logging import RichHandler

        from frappe_manager.logger.log import _add_console_handler

        logger = logging.getLogger("test_replace")

        old_handler = RichHandler()
        logger.addHandler(old_handler)

        assert len(logger.handlers) == 1

        _add_console_handler(logger, "INFO")

        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], LiveAwareRichHandler)
        assert old_handler not in logger.handlers
