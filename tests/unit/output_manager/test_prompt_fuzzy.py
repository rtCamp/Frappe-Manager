"""Tests for prompt_fuzzy() method in all OutputHandler implementations."""

import pytest
from unittest.mock import MagicMock, patch

from frappe_manager.exceptions import NonInteractiveError
from frappe_manager.output_manager import (
    RichOutputHandler,
    JSONOutputHandler,
    SilentOutputHandler,
)
from frappe_manager.output_manager.logging_output import LoggingOutputHandler


class TestRichOutputHandlerPromptFuzzy:
    def test_prompt_fuzzy_non_interactive_with_required_flag_raises_error(self):
        handler = RichOutputHandler()
        handler.set_interactive_mode(non_interactive_flag=True)

        with pytest.raises(NonInteractiveError) as exc_info:
            handler.prompt_fuzzy(
                prompt="Select bench",
                choices=["bench1", "bench2"],
                required_flag="bench_name positional argument",
            )

        assert "bench_name positional argument" in str(exc_info.value)
        assert "Cannot prompt in non-interactive mode" in str(exc_info.value)

    def test_prompt_fuzzy_non_interactive_with_default_returns_default(self):
        handler = RichOutputHandler()
        handler.set_interactive_mode(non_interactive_flag=True)

        result = handler.prompt_fuzzy(prompt="Select bench", choices=["bench1", "bench2"], default="bench1")

        assert result == "bench1"

    def test_prompt_fuzzy_non_interactive_without_default_raises_error(self):
        handler = RichOutputHandler()
        handler.set_interactive_mode(non_interactive_flag=True)

        with pytest.raises(NonInteractiveError) as exc_info:
            handler.prompt_fuzzy(prompt="Select bench", choices=["bench1", "bench2"])

        assert "Cannot prompt in non-interactive mode" in str(exc_info.value)


class TestJSONOutputHandlerPromptFuzzy:
    """Tests for JSONOutputHandler.prompt_fuzzy()."""

    def test_prompt_fuzzy_raises_non_interactive_error_with_required_flag(self):
        """JSONOutputHandler always raises NonInteractiveError with required_flag."""
        handler = JSONOutputHandler()

        with pytest.raises(NonInteractiveError) as exc_info:
            handler.prompt_fuzzy(
                prompt="Select bench",
                choices=["bench1", "bench2"],
                required_flag="bench_name positional argument",
            )

        assert "bench_name positional argument" in str(exc_info.value)

    def test_prompt_fuzzy_returns_default_when_provided(self):
        """JSONOutputHandler returns default if provided."""
        handler = JSONOutputHandler()

        result = handler.prompt_fuzzy(prompt="Select bench", choices=["bench1", "bench2"], default="bench1")

        assert result == "bench1"


class TestSilentOutputHandlerPromptFuzzy:
    """Tests for SilentOutputHandler.prompt_fuzzy()."""

    def test_prompt_fuzzy_raises_error_with_required_flag(self):
        """SilentOutputHandler raises NonInteractiveError with required_flag."""
        handler = SilentOutputHandler()

        with pytest.raises(NonInteractiveError) as exc_info:
            handler.prompt_fuzzy(
                prompt="Select bench",
                choices=["bench1", "bench2"],
                required_flag="bench_name positional argument",
            )

        assert "bench_name positional argument" in str(exc_info.value)

    def test_prompt_fuzzy_returns_default_when_provided(self):
        """SilentOutputHandler returns default if provided."""
        handler = SilentOutputHandler()

        result = handler.prompt_fuzzy(prompt="Select bench", choices=["bench1", "bench2"], default="bench1")

        assert result == "bench1"

    def test_prompt_fuzzy_raises_error_without_default(self):
        """SilentOutputHandler raises error when no default provided."""
        handler = SilentOutputHandler()

        with pytest.raises(NonInteractiveError):
            handler.prompt_fuzzy(prompt="Select bench", choices=["bench1", "bench2"])


class TestLoggingOutputHandlerPromptFuzzy:
    """Tests for LoggingOutputHandler.prompt_fuzzy()."""

    def test_prompt_fuzzy_delegates_to_underlying_handler(self):
        """LoggingOutputHandler delegates prompt_fuzzy to underlying handler."""
        mock_delegate = MagicMock()
        mock_delegate.prompt_fuzzy.return_value = "bench1"
        mock_logger = MagicMock()

        handler = LoggingOutputHandler(delegate=mock_delegate, logger=mock_logger)

        result = handler.prompt_fuzzy(
            prompt="Select bench",
            choices=["bench1", "bench2"],
            default="bench1",
            required_flag="bench_name",
        )

        assert result == "bench1"
        mock_delegate.prompt_fuzzy.assert_called_once_with(
            prompt="Select bench",
            choices=["bench1", "bench2"],
            default="bench1",
            required_flag="bench_name",
        )

    def test_prompt_fuzzy_logs_selection(self, caplog):
        mock_delegate = MagicMock()
        mock_delegate.prompt_fuzzy.return_value = "bench1"
        mock_logger = MagicMock()

        handler = LoggingOutputHandler(delegate=mock_delegate, logger=mock_logger)

        result = handler.prompt_fuzzy(prompt="Select bench", choices=["bench1", "bench2"], default="bench1")

        assert result == "bench1"
        assert mock_logger.info.call_count == 2
        call_messages = [call[0][0] for call in mock_logger.info.call_args_list]
        assert any("Select bench" in msg for msg in call_messages)
        assert any("bench1" in msg for msg in call_messages)
