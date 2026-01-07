"""
Tests for CLI log level flag interactions.

This module tests that the -v/--verbose and --log-level flags interact correctly
in the CLI application callback.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager.commands import app_callback


class TestLogLevelFlagParsing:
    """Test log level flag parsing and validation."""

    def test_no_flags_defaults_to_warning(self):
        """Test that no flags results in WARNING level."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level=None, version=None)

        # When help is called, we just set the defaults
        assert ctx.obj.get("log_level") == "WARNING"
        assert ctx.obj.get("verbose") is False

    def test_single_v_flag_sets_info_level(self):
        """Test that -v sets INFO level."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=1, log_level=None, version=None)

        assert ctx.obj.get("log_level") == "INFO"
        assert ctx.obj.get("verbose") is True

    def test_double_v_flag_sets_debug_level(self):
        """Test that -vv sets DEBUG level."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=2, log_level=None, version=None)

        assert ctx.obj.get("log_level") == "DEBUG"
        assert ctx.obj.get("verbose") is True

    def test_triple_v_flag_still_debug_level(self):
        """Test that -vvv (or more) still results in DEBUG level."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=5, log_level=None, version=None)

        assert ctx.obj.get("log_level") == "DEBUG"
        assert ctx.obj.get("verbose") is True


class TestExplicitLogLevelFlag:
    """Test explicit --log-level flag."""

    def test_log_level_debug_explicit(self):
        """Test --log-level debug sets DEBUG."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="debug", version=None)

        assert ctx.obj.get("log_level") == "DEBUG"
        assert ctx.obj.get("verbose") is True

    def test_log_level_info_explicit(self):
        """Test --log-level info sets INFO."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="info", version=None)

        assert ctx.obj.get("log_level") == "INFO"
        assert ctx.obj.get("verbose") is True

    def test_log_level_warning_explicit(self):
        """Test --log-level warning sets WARNING."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="warning", version=None)

        assert ctx.obj.get("log_level") == "WARNING"
        assert ctx.obj.get("verbose") is False

    def test_log_level_error_explicit(self):
        """Test --log-level error sets ERROR."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="error", version=None)

        assert ctx.obj.get("log_level") == "ERROR"
        assert ctx.obj.get("verbose") is False

    def test_log_level_case_insensitive(self):
        """Test that --log-level is case-insensitive."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        # Test lowercase
        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="debug", version=None)
        assert ctx.obj.get("log_level") == "DEBUG"

        # Test uppercase
        ctx.obj = {}
        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="DEBUG", version=None)
        assert ctx.obj.get("log_level") == "DEBUG"

        # Test mixed case
        ctx.obj = {}
        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="DeBuG", version=None)
        assert ctx.obj.get("log_level") == "DEBUG"


class TestFlagInteractionPrecedence:
    """Test that --log-level takes precedence over -v flags."""

    def test_log_level_overrides_v_flag(self):
        """Test that --log-level warning overrides -v (which would be INFO)."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=1, log_level="warning", version=None)

        assert ctx.obj.get("log_level") == "WARNING"
        assert ctx.obj.get("verbose") is False

    def test_log_level_overrides_vv_flag(self):
        """Test that --log-level warning overrides -vv (which would be DEBUG)."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=2, log_level="warning", version=None)

        assert ctx.obj.get("log_level") == "WARNING"
        assert ctx.obj.get("verbose") is False

    def test_log_level_debug_with_no_v_flag(self):
        """Test that --log-level debug works without -v flag."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="debug", version=None)

        assert ctx.obj.get("log_level") == "DEBUG"
        assert ctx.obj.get("verbose") is True


class TestInvalidLogLevel:
    """Test handling of invalid log levels."""

    def test_invalid_log_level_raises_exit(self):
        """Test that invalid log level shows error and exits."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            with patch("frappe_manager.commands.richprint") as mock_richprint:
                with pytest.raises(typer.Exit) as exc_info:
                    app_callback(ctx, verbose=0, log_level="invalid", version=None)

                assert exc_info.value.exit_code == 1
                mock_richprint.error.assert_called_once()
                error_call = mock_richprint.error.call_args[0][0]
                assert "invalid" in error_call.lower()
                assert "debug" in error_call.lower()
                assert "info" in error_call.lower()


class TestVerboseFlag:
    """Test the verbose flag behavior."""

    def test_verbose_false_for_warning_and_error(self):
        """Test that verbose is False for WARNING and ERROR levels."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        # WARNING level
        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level=None, version=None)
        assert ctx.obj.get("verbose") is False

        # ERROR level
        ctx.obj = {}
        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="error", version=None)
        assert ctx.obj.get("verbose") is False

    def test_verbose_true_for_info_and_debug(self):
        """Test that verbose is True for INFO and DEBUG levels."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        # INFO level
        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=1, log_level=None, version=None)
        assert ctx.obj.get("verbose") is True

        # DEBUG level
        ctx.obj = {}
        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=2, log_level=None, version=None)
        assert ctx.obj.get("verbose") is True

        # Explicit INFO
        ctx.obj = {}
        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="info", version=None)
        assert ctx.obj.get("verbose") is True

        # Explicit DEBUG
        ctx.obj = {}
        with patch("frappe_manager.commands.is_cli_help_called", return_value=True):
            app_callback(ctx, verbose=0, log_level="debug", version=None)
        assert ctx.obj.get("verbose") is True


class TestLoggerConfiguration:
    """Test that Python logger is configured correctly."""

    def test_logger_level_set_correctly(self):
        """Test that logger level is set based on flag."""
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"

        with patch("frappe_manager.commands.is_cli_help_called", return_value=False):
            with patch("frappe_manager.commands.CLI_DIR") as mock_cli_dir:
                with patch("frappe_manager.commands.CLI_BENCHES_DIRECTORY"):
                    with patch("frappe_manager.commands.log.get_logger") as mock_get_logger:
                        with patch("frappe_manager.commands.DockerClient"):
                            with patch("frappe_manager.commands.FMConfigManager"):
                                with patch("frappe_manager.commands.MigrationExecutor") as mock_migration:
                                    with patch("frappe_manager.commands.ServicesManager"):
                                        # Setup mocks
                                        mock_cli_dir.exists.return_value = True
                                        mock_cli_dir.is_dir.return_value = True
                                        mock_logger = MagicMock()
                                        mock_get_logger.return_value = mock_logger
                                        mock_exec = MagicMock()
                                        mock_exec.execute.return_value = True
                                        mock_migration.return_value = mock_exec

                                        # Call with DEBUG level
                                        try:
                                            app_callback(ctx, verbose=2, log_level=None, version=None)
                                        except Exception:
                                            pass  # We're just testing logger.setLevel call

                                        # Verify logger.setLevel was called with DEBUG
                                        mock_logger.setLevel.assert_called_with(logging.DEBUG)
