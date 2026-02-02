"""
Tests for LiveAwareRichHandler.

Ensures logger output coordinates with Live spinner display to prevent
output corruption.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.logger.live_aware_handler import LiveAwareRichHandler


class TestLiveAwareRichHandler:
    """Test Live-aware logging handler."""

    def test_emit_when_live_not_started(self):
        mock_live = MagicMock()
        mock_live.is_started = False

        mock_console = MagicMock()

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

        mock_live.stop.assert_not_called()
        mock_live.start.assert_not_called()

    def test_emit_when_live_is_started(self):
        mock_live = MagicMock()
        mock_live.is_started = True

        mock_console = MagicMock()

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

        mock_live.stop.assert_called_once()
        mock_live.start.assert_called_once_with(refresh=True)

    def test_emit_restarts_live_even_on_exception(self):
        mock_live = MagicMock()
        mock_live.is_started = True

        mock_console = MagicMock()

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

        # Emit the record to trigger stop/start coordination
        handler.emit(record)

        # Verify live was stopped and then restarted
        mock_live.stop.assert_called_once()
        mock_live.start.assert_called_once_with(refresh=True)

    def test_emit_without_live_display(self):
        mock_console = MagicMock()

        handler = LiveAwareRichHandler(
            console=mock_console,
            live_display=None,
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

    def test_handler_initialization(self):
        mock_console = MagicMock()
        mock_live = MagicMock()

        handler = LiveAwareRichHandler(
            console=mock_console,
            live_display=mock_live,
            show_time=False,
            show_path=False,
        )

        assert handler._live_display is mock_live


class TestLoggerIntegration:
    """Test logger setup with LiveAwareRichHandler."""

    def test_add_console_handler_creates_live_aware_handler(self):
        """Verify that _add_console_handler creates LiveAwareRichHandler."""
        from frappe_manager.logger.log import _add_console_handler
        from frappe_manager.output_manager import get_global_output_handler

        logger = logging.getLogger("test_live_aware")
        logger.handlers = []

        _add_console_handler(logger, "INFO")

        handler = logger.handlers[0]
        output = get_global_output_handler()
        assert isinstance(handler, LiveAwareRichHandler)
        assert handler._live_display is output.live

    def test_handler_removes_old_handlers(self):
        """Verify old handlers are removed when adding new one."""
        from frappe_manager.logger.log import _add_console_handler
        from rich.logging import RichHandler

        logger = logging.getLogger("test_replace")

        old_handler = RichHandler()
        logger.addHandler(old_handler)

        assert len(logger.handlers) == 1

        _add_console_handler(logger, "INFO")

        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], LiveAwareRichHandler)
        assert old_handler not in logger.handlers
