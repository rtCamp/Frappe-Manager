"""
Tests for error handling patterns in OutputHandler.

This module documents and tests the two error handling methods:
- display_error(): Display error without raising (for non-fatal errors)
- error(): Display error and raise exception (for fatal errors)
"""

import pytest

from frappe_manager.exceptions import ValidationError
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.silent_output import SilentOutputHandler
from frappe_manager.output_manager.json_output import JSONOutputHandler


class TestDisplayErrorPattern:
    """Test display_error() method - displays error without raising."""

    def test_display_error_does_not_raise_with_rich_handler(self):
        """display_error() shows error but doesn't raise exception."""
        handler = RichOutputHandler()
        
        # Should not raise
        handler.display_error("Something went wrong")
        handler.display_error("Another error")
        
        # Execution continues normally

    def test_display_error_does_not_raise_with_silent_handler(self):
        """display_error() works with silent handler without raising."""
        handler = SilentOutputHandler()
        
        # Should not raise (silently ignored)
        handler.display_error("Error message")

    def test_display_error_does_not_raise_with_json_handler(self):
        """display_error() captures event without raising."""
        handler = JSONOutputHandler()
        
        handler.display_error("Error occurred")
        
        # Should capture event but not raise
        assert len(handler.events) == 1
        assert handler.events[0].event_type == "display_error"


class TestErrorPattern:
    """Test error() method - displays error and raises exception."""

    def test_error_requires_exception_parameter(self):
        """error() requires exception parameter (enforced by type system)."""
        handler = SilentOutputHandler()
        
        # This should be a type error (caught by Pyright):
        # handler.error("Error message")  # Missing required 'exception' parameter
        
        # Correct usage:
        with pytest.raises(ValidationError):
            handler.error("Error message", exception=ValidationError("Test"))

    def test_error_always_raises_with_rich_handler(self):
        """error() displays and raises exception with rich handler."""
        handler = RichOutputHandler()
        
        exc = ValidationError("Invalid configuration")
        
        with pytest.raises(ValidationError, match="Invalid configuration"):
            handler.error("Configuration error", exception=exc)

    def test_error_always_raises_with_silent_handler(self):
        """error() raises exception even with silent handler."""
        handler = SilentOutputHandler()
        
        exc = ValueError("Test error")
        
        with pytest.raises(ValueError, match="Test error"):
            handler.error("Error occurred", exception=exc)

    def test_error_always_raises_with_json_handler(self):
        """error() captures event and raises exception."""
        handler = JSONOutputHandler()
        
        exc = RuntimeError("Critical error")
        
        with pytest.raises(RuntimeError, match="Critical error"):
            handler.error("Fatal error", exception=exc)
        
        # Should still capture event before raising
        assert len(handler.events) == 1
        assert handler.events[0].event_type == "error"
        assert handler.events[0].data["exception_type"] == "RuntimeError"


class TestErrorHandlingUseCases:
    """Test realistic error handling use cases."""

    def test_non_fatal_error_display_only(self):
        """Non-fatal errors are displayed without stopping execution."""
        handler = SilentOutputHandler()
        
        # Example: Service not running (non-fatal, just inform user)
        handler.display_error("Service 'redis' is not running")
        
        # Execution continues...
        handler.print("Continuing with other services")

    def test_fatal_error_raises_exception(self):
        """Fatal errors raise exceptions to stop execution."""
        handler = SilentOutputHandler()
        
        # Example: Critical dependency missing (fatal, stop execution)
        exc = ValidationError("Docker is not installed")
        
        with pytest.raises(ValidationError):
            handler.error("Cannot proceed without Docker", exception=exc)
        
        # Execution stops here (exception propagates)

    def test_multiple_display_errors_before_fatal(self):
        """Can display multiple non-fatal errors before fatal error."""
        handler = SilentOutputHandler()
        
        # Show multiple warnings/errors
        handler.display_error("Warning: Service A failed")
        handler.display_error("Warning: Service B failed")
        handler.display_error("Warning: Service C failed")
        
        # All warnings shown, then fatal error
        with pytest.raises(RuntimeError, match="All services failed"):
            handler.error(
                "Cannot continue - all services failed",
                exception=RuntimeError("All services failed")
            )


class TestMigrationFromOldAPI:
    """Document migration from old error() API to new API."""

    def test_old_style_error_without_exception(self):
        """Old: error() without exception (now replaced with display_error())."""
        handler = SilentOutputHandler()
        
        # Old style (no longer supported):
        # handler.error("Error message")  # Would not raise
        
        # New style:
        handler.display_error("Error message")  # Explicitly non-fatal

    def test_old_style_error_with_exception(self):
        """Old: error() with exception parameter (still works, now required)."""
        handler = SilentOutputHandler()
        
        # Old style (still works):
        with pytest.raises(ValueError):
            handler.error("Error", exception=ValueError("Test"))
        
        # Exception parameter is now required (not optional)

    def test_decision_tree_for_error_handling(self):
        """Document decision tree for choosing error() vs display_error()."""
        handler = SilentOutputHandler()
        
        # Question: Should execution stop?
        
        # NO - use display_error():
        handler.display_error("Optional feature not available")
        handler.display_error("Warning: deprecated API used")
        handler.display_error("Service X is slow")
        
        # YES - use error():
        with pytest.raises(ValidationError):
            handler.error(
                "Critical dependency missing",
                exception=ValidationError("Docker not found")
            )


class TestErrorMessageConsistency:
    """Test that error messages are consistent across handlers."""

    def test_same_error_produces_consistent_output(self):
        """Same error call produces consistent behavior across handlers."""
        exc = ValidationError("Test error")
        
        # Rich handler - displays and raises
        rich = RichOutputHandler()
        with pytest.raises(ValidationError):
            rich.error("Error occurred", exception=exc)
        
        # Silent handler - raises without display
        silent = SilentOutputHandler()
        with pytest.raises(ValidationError):
            silent.error("Error occurred", exception=exc)
        
        # JSON handler - captures and raises
        json = JSONOutputHandler()
        with pytest.raises(ValidationError):
            json.error("Error occurred", exception=exc)
        
        # All handlers raised the same exception

    def test_same_display_error_produces_consistent_output(self):
        """Same display_error() call is consistent across handlers."""
        # Rich handler - displays to console
        rich = RichOutputHandler()
        rich.display_error("Error message")  # Displays, doesn't raise
        
        # Silent handler - silent
        silent = SilentOutputHandler()
        silent.display_error("Error message")  # Silent, doesn't raise
        
        # JSON handler - captures event
        json = JSONOutputHandler()
        json.display_error("Error message")  # Captures, doesn't raise
        assert len(json.events) == 1
        
        # None of them raised an exception
