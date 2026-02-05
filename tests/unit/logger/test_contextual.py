"""
Tests for ContextualLogger.

This module tests the ContextualLogger adapter that wraps logging.Logger
to automatically add context information to all log messages.
"""

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.logger.context import LoggerContext
from frappe_manager.logger.contextual import ContextualLogger


@pytest.fixture
def mock_logger():
    """Create a mock logger for testing."""
    logger = MagicMock(spec=logging.Logger)
    logger.level = logging.INFO
    logger.name = "test_logger"
    return logger


@pytest.fixture
def temp_log_dir():
    """Create temporary directory for file-based logging tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def file_logger(temp_log_dir):
    """Create a real logger that writes to a file for integration testing."""
    logger = logging.getLogger("test_contextual")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    log_file = temp_log_dir / "test.log"
    handler = logging.FileHandler(log_file)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger, log_file


class TestContextualLoggerCreation:
    """Test creation and initialization of ContextualLogger."""

    def test_create_with_context(self, mock_logger):
        """Test creating ContextualLogger with a context."""
        context = LoggerContext(bench="mybench", operation="create")
        contextual = ContextualLogger(mock_logger, context)

        assert contextual.logger == mock_logger
        assert contextual.context == context

    def test_create_without_context(self, mock_logger):
        """Test creating ContextualLogger without context (uses empty context)."""
        contextual = ContextualLogger(mock_logger)

        assert contextual.logger == mock_logger
        assert contextual.context == LoggerContext()  # Empty context

    def test_create_with_none_context(self, mock_logger):
        """Test creating ContextualLogger with None context (uses empty context)."""
        contextual = ContextualLogger(mock_logger, None)

        assert contextual.logger == mock_logger
        assert contextual.context == LoggerContext()  # Empty context


class TestContextualLoggerLogging:
    """Test that ContextualLogger adds context to log messages."""

    def test_info_with_context(self, mock_logger):
        """Test that info() adds context prefix to message."""
        context = LoggerContext(bench="mybench", operation="create")
        contextual = ContextualLogger(mock_logger, context)

        contextual.info("Test message")

        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0][0]
        assert "[bench=mybench]" in call_args
        assert "[op=create]" in call_args
        assert "Test message" in call_args

    def test_debug_with_context(self, mock_logger):
        """Test that debug() adds context prefix to message."""
        context = LoggerContext(bench="mybench")
        contextual = ContextualLogger(mock_logger, context)

        contextual.debug("Debug message")

        mock_logger.debug.assert_called_once()
        call_args = mock_logger.debug.call_args[0][0]
        assert "[bench=mybench]" in call_args
        assert "Debug message" in call_args

    def test_warning_with_context(self, mock_logger):
        """Test that warning() adds context prefix to message."""
        context = LoggerContext(operation="create")
        contextual = ContextualLogger(mock_logger, context)

        contextual.warning("Warning message")

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert "[op=create]" in call_args
        assert "Warning message" in call_args

    def test_error_with_context(self, mock_logger):
        """Test that error() adds context prefix to message."""
        context = LoggerContext(component="docker")
        contextual = ContextualLogger(mock_logger, context)

        contextual.error("Error message")

        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args[0][0]
        assert "[component=docker]" in call_args
        assert "Error message" in call_args

    def test_exception_with_context(self, mock_logger):
        """Test that exception() adds context prefix to message."""
        context = LoggerContext(bench="mybench", operation="create")
        contextual = ContextualLogger(mock_logger, context)

        contextual.exception("Exception occurred")

        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0][0]
        assert "[bench=mybench]" in call_args
        assert "[op=create]" in call_args
        assert "Exception occurred" in call_args


class TestContextualLoggerEmptyContext:
    """Test that ContextualLogger with empty context doesn't add prefix."""

    def test_info_without_context(self, mock_logger):
        """Test that info() with empty context doesn't add prefix."""
        contextual = ContextualLogger(mock_logger)

        contextual.info("Test message")

        mock_logger.info.assert_called_once_with("Test message")

    def test_debug_without_context(self, mock_logger):
        """Test that debug() with empty context doesn't add prefix."""
        contextual = ContextualLogger(mock_logger)

        contextual.debug("Debug message")

        mock_logger.debug.assert_called_once_with("Debug message")

    def test_warning_without_context(self, mock_logger):
        """Test that warning() with empty context doesn't add prefix."""
        contextual = ContextualLogger(mock_logger)

        contextual.warning("Warning message")

        mock_logger.warning.assert_called_once_with("Warning message")

    def test_error_without_context(self, mock_logger):
        """Test that error() with empty context doesn't add prefix."""
        contextual = ContextualLogger(mock_logger)

        contextual.error("Error message")

        mock_logger.error.assert_called_once_with("Error message")


class TestContextualLoggerChildCreation:
    """Test creating child loggers with extended context."""

    def test_child_inherits_parent_context(self, mock_logger):
        """Test that child logger inherits parent's context."""
        parent = ContextualLogger(mock_logger, LoggerContext(bench="mybench", operation="create"))
        child = parent.child(component="docker")

        child.info("Child message")

        call_args = mock_logger.info.call_args[0][0]
        assert "[bench=mybench]" in call_args  # Inherited
        assert "[op=create]" in call_args  # Inherited
        assert "[component=docker]" in call_args  # Added

    def test_child_can_override_parent_context(self, mock_logger):
        """Test that child can override parent's context values."""
        parent = ContextualLogger(mock_logger, LoggerContext(bench="mybench", operation="create"))
        child = parent.child(operation="update")

        child.info("Child message")

        call_args = mock_logger.info.call_args[0][0]
        assert "[bench=mybench]" in call_args  # Inherited
        assert "[op=update]" in call_args  # Overridden
        assert "[op=create]" not in call_args  # Replaced

    def test_child_shares_same_logger_instance(self, mock_logger):
        """Test that child uses same underlying logger as parent."""
        parent = ContextualLogger(mock_logger, LoggerContext(bench="mybench"))
        child = parent.child(component="docker")

        assert child.logger == parent.logger
        assert child.logger is mock_logger

    def test_multiple_children_independent(self, mock_logger):
        """Test that multiple children from same parent are independent."""
        parent = ContextualLogger(mock_logger, LoggerContext(bench="mybench"))
        child1 = parent.child(operation="create")
        child2 = parent.child(operation="delete")

        child1.info("Child 1 message")
        child2.info("Child 2 message")

        # Check first call (child1)
        first_call_args = mock_logger.info.call_args_list[0][0][0]
        assert "[op=create]" in first_call_args
        assert "[op=delete]" not in first_call_args

        # Check second call (child2)
        second_call_args = mock_logger.info.call_args_list[1][0][0]
        assert "[op=delete]" in second_call_args
        assert "[op=create]" not in second_call_args

    def test_grandchild_inherits_through_chain(self, mock_logger):
        """Test that grandchild inherits context through multiple generations."""
        parent = ContextualLogger(mock_logger, LoggerContext(bench="mybench"))
        child = parent.child(operation="create")
        grandchild = child.child(component="docker")

        grandchild.info("Grandchild message")

        call_args = mock_logger.info.call_args[0][0]
        assert "[bench=mybench]" in call_args
        assert "[op=create]" in call_args
        assert "[component=docker]" in call_args

    def test_parent_unchanged_after_child_creation(self, mock_logger):
        """Test that parent context is not modified when creating child."""
        parent = ContextualLogger(mock_logger, LoggerContext(bench="mybench"))
        original_context = parent.context

        child = parent.child(operation="create")

        # Parent should still have original context
        assert parent.context == original_context
        assert parent.context.operation is None
        assert child.context.operation == "create"


class TestContextualLoggerMessageFormatting:
    """Test message formatting with various scenarios."""

    def test_format_message_with_args(self, mock_logger):
        """Test that format arguments are passed through correctly."""
        contextual = ContextualLogger(mock_logger, LoggerContext(bench="mybench"))

        contextual.info("Value: %s", "test")

        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0]
        assert "[bench=mybench]" in call_args[0]
        assert "Value: %s" in call_args[0]
        assert call_args[1] == "test"

    def test_format_message_with_extra_fields(self, mock_logger):
        """Test that extra_fields are appended to message."""
        contextual = ContextualLogger(mock_logger, LoggerContext(bench="mybench"))

        contextual.info("Test message", extra_fields={"custom": "value", "count": 42})

        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0][0]
        assert "[bench=mybench]" in call_args
        assert "Test message" in call_args
        assert "| custom=value" in call_args
        assert "| count=42" in call_args

    def test_full_context_formatting(self, mock_logger):
        """Test formatting with all context fields."""
        contextual = ContextualLogger(
            mock_logger, LoggerContext(bench="mybench", operation="create", component="docker", extra={"step": 1}),
        )

        contextual.info("Test message")

        call_args = mock_logger.info.call_args[0][0]
        assert "[bench=mybench]" in call_args
        assert "[op=create]" in call_args
        assert "[component=docker]" in call_args
        assert "[step=1]" in call_args
        assert "Test message" in call_args


class TestContextualLoggerProperties:
    """Test pass-through properties and methods."""

    def test_level_property(self, mock_logger):
        """Test that level property returns underlying logger's level."""
        mock_logger.level = logging.DEBUG
        contextual = ContextualLogger(mock_logger)

        assert contextual.level == logging.DEBUG

    def test_set_level(self, mock_logger):
        """Test that setLevel() calls underlying logger's setLevel()."""
        contextual = ContextualLogger(mock_logger)

        contextual.setLevel(logging.WARNING)

        mock_logger.setLevel.assert_called_once_with(logging.WARNING)

    def test_is_enabled_for(self, mock_logger):
        """Test that isEnabledFor() calls underlying logger's isEnabledFor()."""
        mock_logger.isEnabledFor.return_value = True
        contextual = ContextualLogger(mock_logger)

        result = contextual.isEnabledFor(logging.INFO)

        assert result is True
        mock_logger.isEnabledFor.assert_called_once_with(logging.INFO)

    def test_name_property(self, mock_logger):
        """Test that name property returns underlying logger's name."""
        mock_logger.name = "test_logger"
        contextual = ContextualLogger(mock_logger)

        assert contextual.name == "test_logger"

    def test_get_context(self, mock_logger):
        """Test that get_context() returns current context."""
        context = LoggerContext(bench="mybench", operation="create")
        contextual = ContextualLogger(mock_logger, context)

        retrieved_context = contextual.get_context()

        assert retrieved_context == context
        assert retrieved_context.bench == "mybench"
        assert retrieved_context.operation == "create"


class TestContextualLoggerFileLogging:
    """Test ContextualLogger with real file logging (integration tests)."""

    def test_info_writes_context_to_file(self, file_logger):
        """Test that info() writes context to log file."""
        logger, log_file = file_logger
        context = LoggerContext(bench="mybench", operation="create")
        contextual = ContextualLogger(logger, context)

        contextual.info("Test message")

        content = log_file.read_text()
        assert "[INFO]" in content
        assert "[bench=mybench]" in content
        assert "[op=create]" in content
        assert "Test message" in content

    def test_debug_writes_context_to_file(self, file_logger):
        """Test that debug() writes context to log file."""
        logger, log_file = file_logger
        context = LoggerContext(bench="mybench")
        contextual = ContextualLogger(logger, context)

        contextual.debug("Debug message")

        content = log_file.read_text()
        assert "[DEBUG]" in content
        assert "[bench=mybench]" in content
        assert "Debug message" in content

    def test_warning_writes_context_to_file(self, file_logger):
        """Test that warning() writes context to log file."""
        logger, log_file = file_logger
        context = LoggerContext(operation="create")
        contextual = ContextualLogger(logger, context)

        contextual.warning("Warning message")

        content = log_file.read_text()
        assert "[WARNING]" in content
        assert "[op=create]" in content
        assert "Warning message" in content

    def test_error_writes_context_to_file(self, file_logger):
        """Test that error() writes context to log file."""
        logger, log_file = file_logger
        context = LoggerContext(component="docker")
        contextual = ContextualLogger(logger, context)

        contextual.error("Error message")

        content = log_file.read_text()
        assert "[ERROR]" in content
        assert "[component=docker]" in content
        assert "Error message" in content

    def test_multiple_messages_all_have_context(self, file_logger):
        """Test that all messages from contextual logger have context."""
        logger, log_file = file_logger
        context = LoggerContext(bench="mybench", operation="create")
        contextual = ContextualLogger(logger, context)

        contextual.info("Message 1")
        contextual.debug("Message 2")
        contextual.warning("Message 3")

        content = log_file.read_text()
        lines = content.strip().split("\n")

        assert len(lines) == 3
        for line in lines:
            assert "[bench=mybench]" in line
            assert "[op=create]" in line

    def test_child_logger_writes_extended_context(self, file_logger):
        """Test that child logger writes extended context to file."""
        logger, log_file = file_logger
        parent = ContextualLogger(logger, LoggerContext(bench="mybench"))
        child = parent.child(operation="create", component="docker")

        child.info("Child message")

        content = log_file.read_text()
        assert "[bench=mybench]" in content
        assert "[op=create]" in content
        assert "[component=docker]" in content


class TestContextualLoggerEdgeCases:
    """Test edge cases and special scenarios."""

    def test_empty_message(self, mock_logger):
        """Test logging an empty message with context."""
        contextual = ContextualLogger(mock_logger, LoggerContext(bench="mybench"))

        contextual.info("")

        call_args = mock_logger.info.call_args[0][0]
        assert "[bench=mybench]" in call_args

    def test_multiline_message(self, mock_logger):
        """Test logging a multiline message with context."""
        contextual = ContextualLogger(mock_logger, LoggerContext(bench="mybench"))

        contextual.info("Line 1\nLine 2\nLine 3")

        call_args = mock_logger.info.call_args[0][0]
        assert "[bench=mybench]" in call_args
        assert "Line 1\nLine 2\nLine 3" in call_args

    def test_message_with_special_characters(self, mock_logger):
        """Test logging message with special characters."""
        contextual = ContextualLogger(mock_logger, LoggerContext(bench="mybench"))

        contextual.info("Message with 'quotes' and \"double quotes\" and %s")

        call_args = mock_logger.info.call_args[0][0]
        assert "[bench=mybench]" in call_args
        assert "Message with 'quotes'" in call_args

    def test_context_with_unicode(self, file_logger):
        """Test context with unicode characters logs correctly."""
        logger, log_file = file_logger
        context = LoggerContext(bench="测试", extra={"user": "admin™"})
        contextual = ContextualLogger(logger, context)

        contextual.info("Unicode test")

        content = log_file.read_text()
        assert "bench=测试" in content
        assert "user=admin™" in content
