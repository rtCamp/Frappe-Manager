"""
Tests for the OutputHandler base interface.

Ensures the abstract base class correctly enforces the interface contract.
"""

import pytest
from frappe_manager.output_manager.base import OutputHandler


class TestOutputHandlerInterface:
    """Tests for the OutputHandler abstract base class."""

    def test_cannot_instantiate_abstract_base_class(self):
        """OutputHandler cannot be instantiated directly."""
        with pytest.raises(TypeError) as exc_info:
            OutputHandler()  # type: ignore

        assert "Can't instantiate abstract class" in str(exc_info.value)

    def test_subclass_must_implement_all_methods(self):
        """Subclass must implement all abstract methods."""

        class IncompleteHandler(OutputHandler):
            """Incomplete implementation missing most methods."""

            def start(self, text: str) -> None:
                pass

        with pytest.raises(TypeError) as exc_info:
            IncompleteHandler()  # type: ignore

        assert "Can't instantiate abstract class" in str(exc_info.value)

    def test_complete_subclass_can_be_instantiated(self):
        """Complete implementation of all methods allows instantiation."""

        class CompleteHandler(OutputHandler):
            """Complete implementation of OutputHandler."""

            def start(self, text: str) -> None:
                pass

            def change_head(self, text: str, style=None) -> None:
                pass

            def update_head(self, text: str) -> None:
                pass

            def stop(self) -> None:
                pass

            def print(self, text: str, emoji_code=":zap:", prefix=None, **kwargs) -> None:
                pass

            def debug(self, text: str, emoji_code=":bug:", **kwargs) -> None:
                pass

            def info(self, text: str, emoji_code=":information:", **kwargs) -> None:
                pass

            def display_error(self, text: str, emoji_code=":no_entry:") -> None:
                pass

            def error(self, text: str, exception: Exception, emoji_code=":no_entry:") -> None:
                if exception:
                    raise exception

            def warning(self, text: str, emoji_code=":warning:") -> None:
                pass

            def live_lines(
                self, data, stdout=True, stderr=True, lines=4, padding=(0, 0, 0, 2), stop_string=None, log_prefix="=>"
            ) -> None:
                pass

            def update_live(self, renderable=None, padding=(0, 0, 0, 0)) -> None:
                pass

            def prompt_ask(self, **kwargs) -> str:
                return ""

            def print_data(self, data, **kwargs) -> None:
                pass

            def print_status(self, text: str, emoji_code=":zap:", **kwargs) -> None:
                pass

        # Should not raise
        handler = CompleteHandler()
        assert isinstance(handler, OutputHandler)
