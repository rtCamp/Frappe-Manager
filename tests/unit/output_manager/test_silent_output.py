"""
Tests for SilentOutputHandler.

Tests the silent output handler to ensure it suppresses all output
while maintaining proper interface behavior.
"""

import pytest

from frappe_manager.output_manager.silent_output import SilentOutputHandler


class TestSilentOutputHandlerBasics:
    """Tests for basic SilentOutputHandler functionality."""

    def test_initialization(self):
        """SilentOutputHandler can be instantiated."""
        handler = SilentOutputHandler()
        assert handler is not None

    def test_start_is_silent(self):
        """start() produces no output."""
        handler = SilentOutputHandler()

        # Should not raise, should not produce output
        handler.start("Starting operation")
        # No assertions needed - just ensure it doesn't fail

    def test_stop_is_silent(self):
        """stop() produces no output."""
        handler = SilentOutputHandler()

        handler.stop()
        # No output expected

    def test_print_is_silent(self):
        """print() produces no output."""
        handler = SilentOutputHandler()

        handler.print("Test message", emoji_code=":rocket:", prefix="PREFIX")
        # No output expected

    def test_display_error_is_silent(self):
        """display_error() produces no output and doesn't raise."""
        handler = SilentOutputHandler()

        handler.display_error("Error message")
        # No output, no exception

    def test_error_with_exception_still_raises(self):
        """error() with exception still raises the exception."""
        handler = SilentOutputHandler()

        with pytest.raises(ValueError, match="Test error"):
            handler.error("Error occurred", exception=ValueError("Test error"))

    def test_warning_is_silent(self):
        """warning() produces no output."""
        handler = SilentOutputHandler()

        handler.warning("Warning message")
        # No output expected


class TestSilentOutputHandlerHeadOperations:
    """Tests for head update operations."""

    def test_change_head_is_silent(self):
        """change_head() produces no output."""
        handler = SilentOutputHandler()

        handler.change_head("Updated text", style="bold")
        # No output expected

    def test_update_head_is_silent(self):
        """update_head() produces no output."""
        handler = SilentOutputHandler()

        handler.update_head("New head")
        # No output expected


class TestSilentOutputHandlerAdvancedOperations:
    """Tests for advanced output operations."""

    def test_live_lines_consumes_iterator(self):
        """live_lines() consumes iterator without output."""
        handler = SilentOutputHandler()

        # Track whether iterator was consumed
        consumed = []

        def data_generator():
            for item in [("stdout", b"Line 1\n"), ("stderr", b"Error 1\n")]:
                consumed.append(item)
                yield item

        handler.live_lines(data_generator())

        # Iterator should be consumed
        assert len(consumed) == 2

    def test_live_lines_respects_stop_string(self):
        """live_lines() stops iteration when stop_string is found."""
        handler = SilentOutputHandler()

        consumed = []

        def data_generator():
            for item in [
                ("stdout", b"Line 1\n"),
                ("stdout", b"STOP HERE\n"),
                ("stdout", b"Line 3\n"),
            ]:
                consumed.append(item)
                yield item

        handler.live_lines(data_generator(), stop_string="STOP")

        # Should stop after "STOP HERE" line
        assert len(consumed) == 2

    def test_update_live_is_silent(self):
        """update_live() produces no output."""
        handler = SilentOutputHandler()

        handler.update_live(renderable="Some content", padding=(1, 2, 3, 4))
        # No output expected

    def test_prompt_ask_returns_default(self):
        """prompt_ask() returns default value when provided."""
        handler = SilentOutputHandler()

        result = handler.prompt_ask(prompt="Enter value", default="test")

        assert result == "test"


class TestSilentOutputHandlerCompleteSequence:
    """Tests for complete operation sequences."""

    def test_complete_operation_sequence(self):
        """Test a complete operation from start to finish."""
        handler = SilentOutputHandler()

        # All operations should execute without errors
        handler.start("Starting operation")
        handler.change_head("Processing")
        handler.print("Step 1 complete")
        handler.warning("Minor issue detected")
        handler.change_head("Finalizing")
        handler.print("Done")
        handler.stop()

        # No exceptions, no output - success

    def test_error_handling_sequence(self):
        """Test error handling without exceptions."""
        handler = SilentOutputHandler()

        handler.start("Operation")
        handler.display_error("Non-fatal error")  # No exception
        handler.warning("Warning")
        handler.stop()

        # All operations complete silently

    def test_multiple_operations(self):
        """Test multiple operations in sequence."""
        handler = SilentOutputHandler()

        # First operation
        handler.start("Operation 1")
        handler.print("Message 1")
        handler.stop()

        # Second operation
        handler.start("Operation 2")
        handler.print("Message 2")
        handler.stop()

        # No state leakage, no output


class TestSilentOutputHandlerIteratorHandling:
    """Tests for iterator handling edge cases."""

    def test_live_lines_with_empty_iterator(self):
        """live_lines() handles empty iterator."""
        handler = SilentOutputHandler()

        data = iter([])

        handler.live_lines(data)
        # Should not raise

    def test_live_lines_with_decode_error(self):
        """live_lines() handles bytes that can't be decoded."""
        handler = SilentOutputHandler()

        # Invalid UTF-8 bytes
        data = iter([("stdout", b"\xff\xfe")])

        # Should not raise even if decoding fails
        try:
            handler.live_lines(data, stop_string="STOP")
        except Exception:
            pytest.fail("live_lines should handle decode errors gracefully")

    def test_live_lines_without_stop_string(self):
        """live_lines() processes all items when no stop_string."""
        handler = SilentOutputHandler()

        consumed = []

        def data_generator():
            for item in [
                ("stdout", b"Line 1\n"),
                ("stdout", b"Line 2\n"),
                ("stdout", b"Line 3\n"),
            ]:
                consumed.append(item)
                yield item

        handler.live_lines(data_generator())

        # All items should be consumed
        assert len(consumed) == 3
