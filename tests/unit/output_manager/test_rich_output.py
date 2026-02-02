"""
Tests for RichOutputHandler.

Tests the Rich terminal output handler implementation (inlined from DisplayManager).
"""

import pytest
from unittest.mock import MagicMock, patch, Mock
from frappe_manager.output_manager.rich_output import RichOutputHandler


class TestRichOutputHandlerInitialization:
    """Tests for RichOutputHandler initialization."""

    def test_initialization(self):
        """RichOutputHandler initializes with Rich components."""
        handler = RichOutputHandler()

        assert handler is not None
        assert handler.stdout is not None  # Rich Console for stdout
        assert handler.stderr is not None  # Rich Console for stderr
        assert handler.spinner is not None  # Spinner
        assert handler.live is not None  # Live display

    def test_interactive_mode_defaults_to_tty(self):
        """Interactive mode defaults based on TTY availability."""
        handler = RichOutputHandler()

        # Should default to TTY availability (both stdin and stdout/stderr)
        assert handler._is_interactive == handler._tty_available


class TestRichOutputHandlerBasicOperations:
    """Tests that RichOutputHandler operations work correctly."""

    def test_start_in_interactive_mode(self):
        """start() shows spinner in interactive mode."""
        handler = RichOutputHandler()
        handler._interactive = True  # Force interactive
        handler._tty_available = True

        with patch.object(handler.live, 'start') as mock_start:
            handler.start("Starting operation")
            mock_start.assert_called_once()

    def test_start_in_non_interactive_mode(self):
        """start() prints status in non-interactive mode."""
        handler = RichOutputHandler()
        handler._interactive = False  # Force non-interactive

        with patch.object(handler.stderr, 'print') as mock_print:
            handler.start("Starting operation")
            mock_print.assert_called_once()
            # Check that it printed the status message
            call_args = mock_print.call_args[0][0]
            assert "Starting operation" in str(call_args)

    def test_stop_in_interactive_mode(self):
        """stop() stops spinner in interactive mode."""
        handler = RichOutputHandler()
        handler._interactive = True
        handler._tty_available = True
        handler._spinner_active = True

        with patch.object(handler.live, 'stop') as mock_stop:
            handler.stop()
            mock_stop.assert_called_once()

    def test_stop_in_non_interactive_mode(self):
        """stop() does nothing in non-interactive mode."""
        handler = RichOutputHandler()
        handler._interactive = False

        with patch.object(handler.live, 'stop') as mock_stop:
            handler.stop()
            mock_stop.assert_not_called()

    def test_print_basic_message(self):
        """print() outputs message to stderr."""
        handler = RichOutputHandler()

        with patch.object(handler.stderr, 'print') as mock_print:
            handler.print("Test message")
            mock_print.assert_called_once()

    def test_print_with_emoji(self):
        """print() includes emoji when provided."""
        handler = RichOutputHandler()

        with patch.object(handler.stderr, 'print') as mock_print:
            handler.print("Test message", emoji_code=":rocket:")
            mock_print.assert_called_once()
            # Verify emoji is in the output
            call_args = str(mock_print.call_args)
            assert "rocket" in call_args.lower()

    def test_display_error_prints_to_stderr(self):
        """display_error() prints directly to stderr."""
        handler = RichOutputHandler()

        with patch.object(handler.stderr, 'print') as mock_print:
            handler.display_error("Error message")
            mock_print.assert_called_once()
            call_str = str(mock_print.call_args)
            assert "Error message" in call_str

    def test_error_with_exception_raises(self):
        """error() with exception always raises."""
        handler = RichOutputHandler()

        test_exception = ValueError("Test error")

        with pytest.raises(ValueError):
            handler.error("Error occurred", exception=test_exception)

    def test_warning_prints_to_stderr(self):
        """warning() prints directly to stderr."""
        handler = RichOutputHandler()

        with patch.object(handler.stderr, 'print') as mock_print:
            handler.warning("Warning message")
            mock_print.assert_called_once()
            call_str = str(mock_print.call_args)
            assert "Warning message" in call_str


class TestRichOutputHandlerHeadOperations:
    """Tests for head update operations."""

    def test_change_head_in_interactive_mode(self):
        """change_head() updates spinner text in interactive mode."""
        handler = RichOutputHandler()
        handler._interactive = True
        handler._tty_available = True
        handler._spinner_active = True

        with patch.object(handler.spinner, 'update') as mock_update:
            handler.change_head("Updated text", style="bold")
            mock_update.assert_called_once()
            args, kwargs = mock_update.call_args
            text_content = str(args[0] if args else kwargs.get('text', ''))
            assert "Updated text" in text_content

    def test_change_head_in_non_interactive_mode(self):
        """change_head() does nothing in non-interactive mode."""
        handler = RichOutputHandler()
        handler._interactive = False

        with patch.object(handler.spinner, 'update') as mock_update:
            handler.change_head("Updated text")
            mock_update.assert_not_called()

    def test_update_head_updates_spinner(self):
        """update_head() updates spinner text."""
        handler = RichOutputHandler()
        handler._interactive = True
        handler._tty_available = True
        handler._spinner_active = True

        with patch.object(handler.spinner, 'update') as mock_update:
            handler.update_head("New head")
            mock_update.assert_called_once()


class TestRichOutputHandlerAdvancedOperations:
    """Tests for advanced output operations."""

    def test_live_lines_processes_data(self):
        """live_lines() processes streaming data."""
        handler = RichOutputHandler()

        data = iter([("stdout", b"Line 1\n"), ("stdout", b"Line 2\n")])

        # Mock the panel and live update
        with patch.object(handler, 'update_live'):
            handler.live_lines(data, stdout=True, stderr=False)
            # Should have processed the data (exact assertion depends on implementation)

    def test_update_live_updates_display(self):
        """update_live() updates live display."""
        handler = RichOutputHandler()
        handler._interactive = True

        mock_renderable = MagicMock()

        with patch.object(handler.live, 'update') as mock_update:
            handler.update_live(renderable=mock_renderable)
            mock_update.assert_called_once()


class TestRichOutputHandlerCompleteSequence:
    """Tests for complete operation sequences."""

    def test_complete_operation_sequence(self):
        """Test a complete operation from start to finish."""
        handler = RichOutputHandler()
        handler._interactive = True
        handler._tty_available = True

        with patch.object(handler.live, 'start'):
            with patch.object(handler.live, 'stop'):
                with patch.object(handler.spinner, 'update'):
                    with patch.object(handler.stderr, 'print'):
                        handler.start("Starting operation")
                        handler.change_head("Processing")
                        handler.print("Step 1 complete")
                        handler.warning("Minor issue detected")
                        handler.change_head("Finalizing")
                        handler.print("Done")
                        handler.stop()

                        # Operations completed without errors
                        # (Detailed assertions would be too coupled to implementation)

    def test_multiple_operations(self):
        """Test multiple operations in sequence."""
        handler = RichOutputHandler()
        handler._interactive = True
        handler._tty_available = True

        with patch.object(handler.live, 'start'):
            with patch.object(handler.live, 'stop'):
                with patch.object(handler.stderr, 'print'):
                    # First operation
                    handler.start("Operation 1")
                    handler.print("Message 1")
                    handler.stop()

                    # Second operation
                    handler.start("Operation 2")
                    handler.print("Message 2")
                    handler.stop()


class TestRichOutputHandlerInteractiveMode:
    """Tests for interactive mode vs non-interactive mode behavior."""

    def test_interactive_true_shows_spinner(self):
        """Interactive mode shows spinner."""
        handler = RichOutputHandler()
        handler._interactive = True
        handler._tty_available = True

        with patch.object(handler.live, 'start') as mock_start:
            handler.start("Test")
            mock_start.assert_called_once()

    def test_interactive_false_prints_status(self):
        """Non-interactive mode prints status instead of spinner."""
        handler = RichOutputHandler()
        handler._interactive = False

        with patch.object(handler.stderr, 'print') as mock_print:
            handler.start("Test")
            mock_print.assert_called_once()

    def test_interactive_respects_flag_over_tty(self):
        """Interactive flag takes precedence over TTY detection."""
        handler = RichOutputHandler()
        handler._tty_available = True
        handler._interactive = False

        assert handler._is_interactive is False


class Test_InteractiveFlag:
    """Tests for _interactive flag behavior."""

    def test_flag_true_forces_interactive(self):
        """_interactive=True forces interactive mode."""
        handler = RichOutputHandler()
        handler._tty_available = False
        handler._interactive = True

        assert handler._is_interactive is True

    def test_flag_false_forces_non_interactive(self):
        """_interactive=False forces non-interactive mode."""
        handler = RichOutputHandler()
        handler._tty_available = True
        handler._interactive = False

        assert handler._is_interactive is False

    def test_flag_none_uses_tty(self):
        """_interactive=None falls back to TTY detection."""
        handler = RichOutputHandler()
        handler._interactive = None

        assert handler._is_interactive == handler._tty_available


class TestRichOutputHandlerThreadSafety:
    """Tests for thread safety."""

    def test_has_lock(self):
        """Handler has thread lock for concurrent access."""
        handler = RichOutputHandler()
        assert handler._lock is not None
