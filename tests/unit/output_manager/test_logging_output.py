"""
Tests for LoggingOutputHandler.

This module tests the LoggingOutputHandler wrapper that automatically logs
all OutputHandler method calls to a file logger.
"""

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.exceptions import ValidationError
from frappe_manager.logger.log import FMLogger
from frappe_manager.output_manager.logging_output import LoggingOutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.silent_output import SilentOutputHandler


@pytest.fixture
def temp_log_dir():
    """Create temporary directory for logs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_logger(temp_log_dir):
    """Create a test logger instance."""
    # Create a simple logger for testing
    logger = logging.getLogger("test_logging_output")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()  # Clear any existing handlers

    # Add file handler
    log_file = temp_log_dir / "test.log"
    handler = logging.FileHandler(log_file)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger, log_file


def make_handler(delegate, logger, log_prefix="[OUTPUT]"):
    """LoggingOutputHandler wired to a test logger (ambient API takes none)."""
    handler = LoggingOutputHandler(delegate, log_prefix=log_prefix)
    handler.logger = FMLogger(logger, component="output")
    return handler


class TestLoggingOutputHandlerBasics:
    """Test basic functionality of LoggingOutputHandler."""

    def test_init_with_verbose_true(self, test_logger):
        """Test initialization with verbose=True."""
        logger, _ = test_logger
        rich = RichOutputHandler(verbose=True)
        output = make_handler(rich, logger)

        assert output.verbose is True
        assert output.delegate == rich
        # Logger is now wrapped in ContextualLogger
        assert isinstance(output.logger, FMLogger)
        assert output.logger.logger == logger
        assert output.log_prefix == "[OUTPUT]"

    def test_init_with_verbose_false(self, test_logger):
        """Test initialization with verbose=False."""
        logger, _ = test_logger
        rich = RichOutputHandler(verbose=False)
        output = make_handler(rich, logger)

        assert output.verbose is False

    def test_init_with_custom_prefix(self, test_logger):
        """Test initialization with custom log prefix."""
        logger, _ = test_logger
        rich = RichOutputHandler()
        output = make_handler(rich, logger, log_prefix="[CUSTOM]")

        assert output.log_prefix == "[CUSTOM]"


class TestLoggingOutputHandlerDelegation:
    """Test that LoggingOutputHandler delegates to wrapped handler."""

    def test_delegates_start(self, test_logger):
        """Test that start() delegates to wrapped handler."""
        logger, _ = test_logger
        mock_handler = MagicMock(spec=RichOutputHandler)
        mock_handler.verbose = False
        output = make_handler(mock_handler, logger)

        output.start("Starting operation")

        mock_handler.start.assert_called_once_with("Starting operation")

    def test_delegates_print(self, test_logger):
        """Test that print() delegates to wrapped handler."""
        logger, _ = test_logger
        mock_handler = MagicMock(spec=RichOutputHandler)
        mock_handler.verbose = False
        output = make_handler(mock_handler, logger)

        output.print("Test message", emoji_code=":rocket:")

        mock_handler.print.assert_called_once_with("Test message", ":rocket:", None)

    def test_delegates_error(self, test_logger):
        """Test that error() delegates and raises exception."""
        logger, _ = test_logger
        mock_handler = MagicMock(spec=RichOutputHandler)
        mock_handler.verbose = False
        mock_handler.error.side_effect = ValidationError("Test error")
        output = make_handler(mock_handler, logger)

        with pytest.raises(ValidationError, match="Test error"):
            output.error("Error occurred", ValidationError("Test error"))

        mock_handler.error.assert_called_once()


class TestLoggingOutputHandlerFileLogging:
    """Test that LoggingOutputHandler logs to file at correct levels."""

    def test_print_logs_at_info_level(self, test_logger):
        """Test that print() logs at INFO level."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        output.print("Test message")

        content = log_file.read_text()
        assert "[INFO] [OUTPUT] Test message" in content

    def test_debug_logs_at_debug_level(self, test_logger):
        """Test that debug() logs at DEBUG level."""
        logger, log_file = test_logger
        rich = SilentOutputHandler(verbose=True)
        output = make_handler(rich, logger)

        output.debug("Debug information")

        content = log_file.read_text()
        assert "[DEBUG] [OUTPUT] Debug information" in content

    def test_info_logs_at_info_level(self, test_logger):
        """Test that info() logs at INFO level."""
        logger, log_file = test_logger
        rich = SilentOutputHandler(verbose=True)
        output = make_handler(rich, logger)

        output.info("Info message")

        content = log_file.read_text()
        assert "[INFO] [OUTPUT] Info message" in content

    def test_warning_logs_at_warning_level(self, test_logger):
        """Test that warning() logs at WARNING level."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        output.warning("Warning message")

        content = log_file.read_text()
        assert "[WARNING] [OUTPUT] Warning message" in content

    def test_display_error_logs_at_error_level(self, test_logger):
        """Test that display_error() logs at ERROR level."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        output.display_error("Error message")

        content = log_file.read_text()
        assert "[ERROR] [OUTPUT] Error message" in content

    def test_error_logs_exception_details(self, test_logger):
        """Test that error() logs exception details."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        exc = ValidationError("Invalid input")

        with pytest.raises(ValidationError):
            output.error("Operation failed", exc)

        content = log_file.read_text()
        assert "[ERROR] [OUTPUT] Operation failed" in content
        assert "ValidationError" in content
        assert "Invalid input" in content


class TestLoggingOutputHandlerWithPrefix:
    """Test logging with message prefixes."""

    def test_print_with_prefix_logs_full_message(self, test_logger):
        """Test that print() with prefix logs the full message."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        output.print("Creating bench", prefix="✓")

        content = log_file.read_text()
        assert "[INFO] [OUTPUT] ✓ Creating bench" in content

    def test_custom_log_prefix(self, test_logger):
        """Test custom log prefix in messages."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger, log_prefix="[CUSTOM]")

        output.print("Test")

        content = log_file.read_text()
        assert "[INFO] [CUSTOM] Test" in content


class TestLoggingOutputHandlerOperationMethods:
    """Test logging of operation lifecycle methods."""

    def test_start_logs_at_info_level(self, test_logger):
        """Test that start() logs at INFO level."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        output.start("Starting operation")

        content = log_file.read_text()
        assert "[INFO] [OUTPUT] START: Starting operation" in content

    def test_change_head_logs_at_debug_level(self, test_logger):
        """Test that change_head() logs at DEBUG level."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        output.change_head("New status")

        content = log_file.read_text()
        assert "[DEBUG] [OUTPUT] CHANGE_HEAD: New status" in content

    def test_update_head_logs_at_debug_level(self, test_logger):
        """Test that update_head() logs at DEBUG level."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        output.update_head("Updated status")

        content = log_file.read_text()
        assert "[DEBUG] [OUTPUT] UPDATE_HEAD: Updated status" in content

    def test_stop_logs_at_debug_level(self, test_logger):
        """Test that stop() logs at DEBUG level."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        output.stop()

        content = log_file.read_text()
        assert "[DEBUG] [OUTPUT] STOP" in content


class TestLoggingOutputHandlerLiveLines:
    """Test live_lines logging."""

    def test_live_lines_logs_start_and_completion(self, test_logger):
        """Test that live_lines() logs start and completion."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        # Create iterator with sample data
        data = iter([("stdout", b"line 1\n"), ("stdout", b"line 2\n")])

        output.live_lines(data)

        content = log_file.read_text()
        assert "[DEBUG] [OUTPUT] LIVE_LINES: Starting" in content
        assert "[DEBUG] [OUTPUT] LIVE_LINES: Completed" in content


class TestLoggingOutputHandlerPromptSecurity:
    """Test that password prompts don't log responses."""

    def test_prompt_ask_logs_prompt(self, test_logger):
        """Test that prompt_ask() logs the prompt text."""
        logger, log_file = test_logger
        rich = MagicMock(spec=RichOutputHandler)
        rich.verbose = False
        rich.prompt_ask.return_value = "user_input"
        output = make_handler(rich, logger)

        result = output.prompt_ask(prompt="Enter username: ")

        assert result == "user_input"
        content = log_file.read_text()
        assert "[INFO] [OUTPUT] PROMPT: Enter username:" in content
        assert "[INFO] [OUTPUT] PROMPT_RESPONSE: user_input" in content

    def test_prompt_ask_hides_password_response(self, test_logger):
        """Test that password prompts hide the response in logs."""
        logger, log_file = test_logger
        rich = MagicMock(spec=RichOutputHandler)
        rich.verbose = False
        rich.prompt_ask.return_value = "secret123"
        output = make_handler(rich, logger)

        result = output.prompt_ask(prompt="Enter password: ")

        assert result == "secret123"
        content = log_file.read_text()
        assert "[INFO] [OUTPUT] PROMPT: Enter password:" in content
        assert "[INFO] [OUTPUT] PROMPT_RESPONSE: [password hidden]" in content
        assert "secret123" not in content


class TestLoggingOutputHandlerDisplayError:
    """Test display_error() method that displays without raising."""

    def test_display_error_logs_without_raising(self, test_logger):
        """Test that display_error() logs message without raising exception."""
        logger, log_file = test_logger
        rich = SilentOutputHandler()
        output = make_handler(rich, logger)

        # Call display_error (should not raise)
        output.display_error("Error occurred")

        content = log_file.read_text()
        assert "[ERROR] [OUTPUT] Error occurred" in content


class TestLoggingOutputHandlerInteractiveMode:
    """Test that set_interactive_mode forwards to delegate."""

    def test_set_interactive_mode_forwards_to_delegate(self, test_logger):
        """Test that set_interactive_mode is forwarded to the delegate RichOutputHandler."""
        logger, _ = test_logger
        rich = RichOutputHandler()
        output = make_handler(rich, logger)

        output.set_interactive_mode(non_interactive_flag=True)

        assert output._interactive is False
        assert rich._interactive is False

    def test_set_interactive_mode_wrapper_only(self, test_logger):
        """Test that wrapper respects non_interactive_flag."""
        logger, _ = test_logger
        rich = RichOutputHandler()
        output = make_handler(rich, logger)

        output.set_interactive_mode(non_interactive_flag=False)

        assert output._interactive == rich._tty_available
        assert rich._interactive == rich._tty_available

    def test_delegate_spinner_respects_forwarded_flag(self, test_logger):
        """Test that delegate's spinner behavior respects forwarded interactive flag."""
        logger, _ = test_logger
        rich = RichOutputHandler()
        output = make_handler(rich, logger)

        output.set_interactive_mode(non_interactive_flag=True)

        assert rich._is_interactive is False


class TestLoggingOutputHandlerExit:

    def test_exit_delegates_to_rich_handler(self, test_logger):
        logger, log_file = test_logger
        rich = RichOutputHandler()
        output = make_handler(rich, logger)

        with pytest.raises(Exception):
            output.exit("Test error message")

        log_contents = log_file.read_text()
        assert "[ERROR] [OUTPUT] EXIT: Test error message" in log_contents

    def test_exit_with_error_msg(self, test_logger):
        logger, log_file = test_logger
        rich = RichOutputHandler()
        output = make_handler(rich, logger)

        with pytest.raises(Exception):
            output.exit("Test error", error_msg="Additional details")

        log_contents = log_file.read_text()
        assert "[ERROR] [OUTPUT] EXIT: Test error | Error: Additional details" in log_contents

    def test_exit_with_silent_handler_fallback(self, test_logger):
        logger, log_file = test_logger
        silent = SilentOutputHandler()
        output = make_handler(silent, logger)

        with pytest.raises(Exception):
            output.exit("Test error message")

        log_contents = log_file.read_text()
        assert "[ERROR] [OUTPUT] EXIT: Test error message" in log_contents
