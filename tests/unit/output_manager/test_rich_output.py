"""
Tests for RichOutputHandler.

Tests the Rich terminal output handler to ensure it properly wraps
the existing DisplayManager (richprint).
"""

import pytest
from unittest.mock import MagicMock, patch
from frappe_manager.output_manager.rich_output import RichOutputHandler


class TestRichOutputHandlerInitialization:
    """Tests for RichOutputHandler initialization."""

    def test_initialization(self):
        """RichOutputHandler initializes with richprint."""
        handler = RichOutputHandler()

        assert handler is not None
        assert handler._richprint is not None


class TestRichOutputHandlerDelegation:
    """Tests that RichOutputHandler properly delegates to richprint."""

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_start_delegates_to_richprint(self, mock_richprint):
        """start() delegates to richprint.start()."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.start("Starting operation")

        mock_richprint.start.assert_called_once_with("Starting operation")

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_stop_delegates_to_richprint(self, mock_richprint):
        """stop() delegates to richprint.stop()."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.stop()

        mock_richprint.stop.assert_called_once()

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_print_delegates_to_richprint(self, mock_richprint):
        """print() delegates to richprint.print()."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.print("Test message", emoji_code=":rocket:", prefix="PREFIX")

        mock_richprint.print.assert_called_once_with("Test message", emoji_code=":rocket:", prefix="PREFIX")

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_print_with_kwargs_delegates_to_richprint(self, mock_richprint):
        """print() with extra kwargs delegates correctly."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.print("Message", style="bold", highlight=True)

        mock_richprint.print.assert_called_once()
        call_kwargs = mock_richprint.print.call_args.kwargs
        assert "style" in call_kwargs
        assert "highlight" in call_kwargs

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_display_error_delegates_to_richprint(self, mock_richprint):
        """display_error() delegates to richprint.print()."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.display_error("Error message", emoji_code=":no_entry:")

        mock_richprint.print.assert_called_once_with("Error message", emoji_code=":no_entry:")

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_error_with_exception_delegates_to_richprint(self, mock_richprint):
        """error() with exception delegates to richprint.error()."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        test_exception = ValueError("Test error")

        # richprint.error will raise the exception, so we need to mock that
        mock_richprint.error.side_effect = test_exception

        with pytest.raises(ValueError):
            handler.error("Error occurred", exception=test_exception)

        mock_richprint.error.assert_called_once()

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_warning_delegates_to_richprint(self, mock_richprint):
        """warning() delegates to richprint.warning()."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.warning("Warning message")

        mock_richprint.warning.assert_called_once_with("Warning message", emoji_code=":warning:")


class TestRichOutputHandlerHeadOperations:
    """Tests for head update operations."""

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_change_head_delegates_to_richprint(self, mock_richprint):
        """change_head() delegates to richprint.change_head()."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.change_head("Updated text", style="bold")

        mock_richprint.change_head.assert_called_once_with("Updated text", style="bold")

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_change_head_without_style_delegates_to_richprint(self, mock_richprint):
        """change_head() without style delegates correctly."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.change_head("Updated text")

        mock_richprint.change_head.assert_called_once_with("Updated text", style=None)

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_update_head_delegates_to_richprint(self, mock_richprint):
        """update_head() delegates to richprint.update_head()."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.update_head("New head")

        mock_richprint.update_head.assert_called_once_with("New head")


class TestRichOutputHandlerAdvancedOperations:
    """Tests for advanced output operations."""

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_live_lines_delegates_to_richprint(self, mock_richprint):
        """live_lines() delegates to richprint.live_lines()."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        data = iter([("stdout", b"Line 1\n")])

        handler.live_lines(
            data=data, stdout=True, stderr=False, lines=10, padding=(1, 2, 3, 4), stop_string="STOP", log_prefix="=>"
        )

        mock_richprint.live_lines.assert_called_once()
        call_kwargs = mock_richprint.live_lines.call_args.kwargs
        assert call_kwargs["stdout"] is True
        assert call_kwargs["stderr"] is False
        assert call_kwargs["lines"] == 10
        assert call_kwargs["padding"] == (1, 2, 3, 4)
        assert call_kwargs["stop_string"] == "STOP"
        assert call_kwargs["log_prefix"] == "=>"

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_update_live_delegates_to_richprint(self, mock_richprint):
        """update_live() delegates to richprint.update_live()."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        mock_renderable = MagicMock()

        handler.update_live(renderable=mock_renderable, padding=(1, 2, 3, 4))

        mock_richprint.update_live.assert_called_once_with(renderable=mock_renderable, padding=(1, 2, 3, 4))

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_prompt_ask_delegates_to_richprint(self, mock_richprint):
        """prompt_ask() delegates to richprint.prompt_ask() in interactive mode."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint
        handler._tty_available = True
        handler._interactive = True
        mock_richprint.prompt_ask.return_value = "user_input"

        result = handler.prompt_ask(prompt="Enter value", default="test")

        assert result == "user_input"
        mock_richprint.prompt_ask.assert_called_once()
        call_kwargs = mock_richprint.prompt_ask.call_args.kwargs
        assert "prompt" in call_kwargs
        assert "default" in call_kwargs


class TestRichOutputHandlerCompleteSequence:
    """Tests for complete operation sequences."""

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_complete_operation_sequence(self, mock_richprint):
        """Test a complete operation from start to finish."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.start("Starting operation")
        handler.change_head("Processing")
        handler.print("Step 1 complete")
        handler.warning("Minor issue detected")
        handler.change_head("Finalizing")
        handler.print("Done")
        handler.stop()

        # Verify all methods were called
        assert mock_richprint.start.call_count == 1
        assert mock_richprint.change_head.call_count == 2
        assert mock_richprint.print.call_count == 2
        assert mock_richprint.warning.call_count == 1
        assert mock_richprint.stop.call_count == 1

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_multiple_operations(self, mock_richprint):
        """Test multiple operations in sequence."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        # First operation
        handler.start("Operation 1")
        handler.print("Message 1")
        handler.stop()

        # Second operation
        handler.start("Operation 2")
        handler.print("Message 2")
        handler.stop()

        # Verify call counts
        assert mock_richprint.start.call_count == 2
        assert mock_richprint.print.call_count == 2
        assert mock_richprint.stop.call_count == 2


class TestRichOutputHandlerDefaultParameters:
    """Tests that default parameters are properly passed."""

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_print_default_emoji_code(self, mock_richprint):
        """print() uses default emoji_code if not provided."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.print("Message")

        call_kwargs = mock_richprint.print.call_args.kwargs
        assert call_kwargs.get("emoji_code") == ":zap:"

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_error_default_emoji_code(self, mock_richprint):
        """display_error() uses default emoji_code if not provided."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.display_error("Error")

        call_kwargs = mock_richprint.print.call_args.kwargs
        assert call_kwargs.get("emoji_code") == ":no_entry:"

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_warning_default_emoji_code(self, mock_richprint):
        """warning() uses default emoji_code if not provided."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        handler.warning("Warning")

        call_kwargs = mock_richprint.warning.call_args.kwargs
        assert call_kwargs.get("emoji_code") == ":warning:"

    @patch('frappe_manager.output_manager.rich_output.richprint')
    def test_live_lines_default_parameters(self, mock_richprint):
        """live_lines() uses default parameters if not provided."""
        handler = RichOutputHandler()
        handler._richprint = mock_richprint

        data = iter([])
        handler.live_lines(data)

        call_kwargs = mock_richprint.live_lines.call_args.kwargs
        assert call_kwargs["stdout"] is True
        assert call_kwargs["stderr"] is True
        assert call_kwargs["lines"] == 4
        assert call_kwargs["padding"] == (0, 0, 0, 2)
        assert call_kwargs["log_prefix"] == "=>"
