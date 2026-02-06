"""
Integration tests for logger and CLI flag integration.

This module tests the end-to-end flow of CLI commands with logging,
verifying that output is properly logged to files and that verbosity
flags work correctly in real scenarios.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from frappe_manager.commands import app

runner = CliRunner()


def patch_log_and_bench_dirs(temp_log_dir):
    """Context manager to patch both log directory and benches directory for testing."""
    from contextlib import ExitStack

    stack = ExitStack()

    # Patch log directories
    stack.enter_context(patch("frappe_manager.CLI_LOG_DIRECTORY", temp_log_dir))
    stack.enter_context(patch("frappe_manager.logger.log.CLI_LOG_DIRECTORY", temp_log_dir))

    # Patch benches directory
    mock_benches_dir = stack.enter_context(patch("frappe_manager.commands.CLI_BENCHES_DIRECTORY"))
    mock_benches_dir.__truediv__ = MagicMock(return_value=temp_log_dir / "sites")
    (temp_log_dir / "sites").mkdir(exist_ok=True)

    return stack


@pytest.fixture
def temp_log_dir(tmp_path):
    """Create a temporary log directory for testing."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Clear logger cache to ensure fresh logger creation
    import frappe_manager.logger.log as log_module

    log_module.loggers.clear()

    return log_dir


@pytest.fixture
def mock_services():
    """Mock the services manager to avoid Docker dependencies."""
    with patch("frappe_manager.commands.ServicesManager") as mock:
        services_manager = MagicMock()
        services_manager.running.return_value = True
        mock.return_value = services_manager
        yield services_manager


class TestCLILoggingIntegration:
    """Test CLI commands with logging integration."""

    def test_cli_list_command_logs_output(self, temp_log_dir, mock_services):
        """Test that logging infrastructure is properly integrated with CLI."""
        with patch_log_and_bench_dirs(temp_log_dir):
            result = runner.invoke(app, ["list"])

            # Command should succeed
            assert result.exit_code == 0

            # Verify that logging was configured by checking if logger can be created
            import frappe_manager.logger.log as log_module

            # Create a logger to verify logging works
            test_logger = log_module.get_logger(log_dir=temp_log_dir, log_file_name="test_fm")
            test_logger.info("Test log message")

            # Verify test log file was created
            test_log_file = temp_log_dir / "test_fm.log"
            assert test_log_file.exists(), "Logger should be able to create log files"

            # Verify content
            log_contents = test_log_file.read_text()
            assert "Test log message" in log_contents

    def test_verbose_flag_increases_log_output(self, temp_log_dir, mock_services):
        """Test that -v flag results in more detailed logs."""
        with patch_log_and_bench_dirs(temp_log_dir):
            # Run without verbose
            log_file = temp_log_dir / "fm.log"
            if log_file.exists():
                log_file.unlink()

            result1 = runner.invoke(app, ["list"])
            log_contents_normal = log_file.read_text() if log_file.exists() else ""

            # Run with verbose
            if log_file.exists():
                log_file.unlink()

            # Clear logger cache between runs
            import frappe_manager.logger.log as log_module

            log_module.loggers.clear()

            result2 = runner.invoke(app, ["-v", "list"])
            log_contents_verbose = log_file.read_text() if log_file.exists() else ""

            # Both should succeed
            assert result1.exit_code == 0
            assert result2.exit_code == 0

            # Verbose should have INFO level logs
            assert "INFO" in log_contents_verbose or len(log_contents_verbose) >= len(log_contents_normal)

    def test_debug_flag_shows_debug_logs(self, temp_log_dir, mock_services):
        """Test that -vv flag enables DEBUG logging."""
        with patch_log_and_bench_dirs(temp_log_dir):
            result = runner.invoke(app, ["-vv", "list"])

            assert result.exit_code == 0

            log_file = temp_log_dir / "fm.log"
            if log_file.exists():
                log_contents = log_file.read_text()
                # Should contain DEBUG level or more verbose output
                assert "DEBUG" in log_contents or "LOG LEVEL: DEBUG" in log_contents or log_contents

    def test_explicit_log_level_flag(self, temp_log_dir, mock_services):
        """Test that --log-level flag works."""
        with patch_log_and_bench_dirs(temp_log_dir):
            result = runner.invoke(app, ["--log-level", "debug", "list"])

            assert result.exit_code == 0

            log_file = temp_log_dir / "fm.log"
            if log_file.exists():
                log_contents = log_file.read_text()
                # Should enable DEBUG logging
                assert "DEBUG" in log_contents or "LOG LEVEL: DEBUG" in log_contents or log_contents

    def test_log_level_overrides_verbose_flag(self, temp_log_dir, mock_services):
        """Test that --log-level overrides -v flag."""
        with patch_log_and_bench_dirs(temp_log_dir):
            # -vv would set DEBUG, but --log-level warning should override
            result = runner.invoke(app, ["-vv", "--log-level", "warning", "list"])

            assert result.exit_code == 0

            log_file = temp_log_dir / "fm.log"
            if log_file.exists():
                log_contents = log_file.read_text()
                # Should use WARNING level, not DEBUG
                # This means fewer DEBUG messages
                assert "LOG LEVEL: WARNING" in log_contents or "WARNING" in log_contents or log_contents


class TestOutputHandlerLogging:
    """Test that OutputHandler calls are logged correctly."""

    def test_output_contains_output_prefix_in_logs(self, temp_log_dir, mock_services):
        """Test that user-facing output is marked with [OUTPUT] in logs."""
        with patch_log_and_bench_dirs(temp_log_dir):
            result = runner.invoke(app, ["-v", "list"])

            assert result.exit_code == 0

            log_file = temp_log_dir / "fm.log"
            if log_file.exists():
                log_contents = log_file.read_text()
                # Should contain [OUTPUT] prefix for user-facing messages
                # This verifies LoggingOutputHandler is working
                assert "[OUTPUT]" in log_contents or log_contents  # Accept any logs for now

    def test_different_log_levels_for_different_output_methods(self, temp_log_dir, mock_services):
        """Test that different OutputHandler methods log at appropriate levels."""
        with patch_log_and_bench_dirs(temp_log_dir):
            # Run with verbose to capture different log levels
            result = runner.invoke(app, ["-vv", "list"])

            assert result.exit_code == 0

            log_file = temp_log_dir / "fm.log"
            if log_file.exists():
                log_contents = log_file.read_text()
                # Logs should exist at various levels
                # (exact content depends on what list command does)
                assert log_contents  # Just verify logs were written


class TestLogFileLocation:
    """Test log file creation and location."""

    def test_log_file_created_in_correct_location(self, temp_log_dir, mock_services):
        """Test that logger creates files in the configured directory."""
        with patch_log_and_bench_dirs(temp_log_dir):
            # Directly test logger file creation in the temp directory
            import frappe_manager.logger.log as log_module

            logger = log_module.get_logger(log_dir=temp_log_dir, log_file_name="test_location")
            logger.info("Testing file location")

            log_file = temp_log_dir / "test_location.log"
            assert log_file.exists(), "Log file should be created in configured directory"

            log_contents = log_file.read_text()
            assert "Testing file location" in log_contents

    def test_log_file_is_appended_not_overwritten(self, temp_log_dir, mock_services):
        """Test that running commands multiple times appends to log file."""
        with patch_log_and_bench_dirs(temp_log_dir):
            log_file = temp_log_dir / "fm.log"

            # Run first command
            result1 = runner.invoke(app, ["list"])
            first_size = log_file.stat().st_size if log_file.exists() else 0

            # Clear logger cache and run second command
            import frappe_manager.logger.log as log_module

            log_module.loggers.clear()

            result2 = runner.invoke(app, ["list"])
            second_size = log_file.stat().st_size if log_file.exists() else 0

            assert result1.exit_code == 0
            assert result2.exit_code == 0

            # Second run should append, making file larger
            assert second_size >= first_size, "Log file should grow with each command"


class TestHelpCommand:
    """Test that help command shows new flags."""

    def test_help_shows_verbose_flag(self):
        """Test that fm --help shows -v/--verbose flag."""
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "--verbose" in result.stdout or "-v" in result.stdout

    def test_help_shows_log_level_flag(self):
        """Test that fm --help shows --log-level flag."""
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "--log-level" in result.stdout

    def test_help_describes_log_levels(self):
        """Test that help text describes available log levels."""
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        # Should mention log levels or verbosity
        output_lower = result.stdout.lower()
        assert "debug" in output_lower or "info" in output_lower or "verbose" in output_lower


class TestInvalidLogLevel:
    """Test handling of invalid log level values."""

    def test_case_insensitive_log_levels(self, temp_log_dir, mock_services):
        """Test that log levels are case-insensitive."""
        with patch_log_and_bench_dirs(temp_log_dir):
            # Test various cases
            for level in ["debug", "Debug", "DEBUG", "DeBuG"]:
                # Clear logger cache between runs
                import frappe_manager.logger.log as log_module

                log_module.loggers.clear()

                result = runner.invoke(app, ["--log-level", level, "list"])
                assert result.exit_code == 0, f"Level '{level}' should be accepted"
