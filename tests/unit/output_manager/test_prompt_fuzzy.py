"""Tests for prompt_fuzzy() method in all OutputHandler implementations."""

import io
from unittest.mock import MagicMock

import pytest

from frappe_manager.exceptions import NonInteractiveError
from frappe_manager.output_manager import (
    RichOutputHandler,
    SilentOutputHandler,
)
from frappe_manager.output_manager.sink_handler import SinkOutputHandler
from frappe_manager.output_manager.sinks import EventSink


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

        assert any("bench_name positional argument" in s for s in exc_info.value.suggestions)
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
        handler = SinkOutputHandler(terminal=None, sinks=[EventSink(io.StringIO())])

        with pytest.raises(NonInteractiveError) as exc_info:
            handler.prompt_fuzzy(
                prompt="Select bench",
                choices=["bench1", "bench2"],
                required_flag="bench_name positional argument",
            )

        assert any("bench_name positional argument" in s for s in exc_info.value.suggestions)

    def test_prompt_fuzzy_returns_default_when_provided(self):
        """JSONOutputHandler returns default if provided."""
        handler = SinkOutputHandler(terminal=None, sinks=[EventSink(io.StringIO())])

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

        assert any("bench_name positional argument" in s for s in exc_info.value.suggestions)

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


class TestTheLiveHandlerPromptFuzzy:
    """`prompt_fuzzy` on the handler commands actually hold."""

    def test_it_delegates_to_the_terminal(self):
        terminal = MagicMock()
        terminal.prompt_fuzzy.return_value = "bench1"
        handler = SinkOutputHandler(terminal=terminal, sinks=[])

        result = handler.prompt_fuzzy(
            prompt="Select bench",
            choices=["bench1", "bench2"],
            default="bench1",
            required_flag="bench_name",
        )

        assert result == "bench1"
        terminal.prompt_fuzzy.assert_called_once_with(
            prompt="Select bench",
            choices=["bench1", "bench2"],
            default="bench1",
            required_flag="bench_name",
        )

    def test_a_default_answers_it_when_there_is_no_terminal(self):
        """A default IS the answer; refusing when one exists fails a runnable headless command
        over a question that was already settled."""
        handler = SinkOutputHandler(terminal=None, sinks=[])

        assert handler.prompt_fuzzy(prompt="Select bench", choices=["a", "b"], default="a") == "a"


@pytest.mark.unit
class TestWhatAnswersAHeadlessPrompt:
    """`prompt_ask` gates state changes and `prompt_fuzzy` picks from a list, so they may not
    treat a default the same way. A green suite once shipped them identical."""

    def test_yes_answers_a_confirmation_without_a_terminal(self):
        """--yes means "do the thing I typed": the refusal used to be raised before the flag was
        ever read, which made it useless in a script."""
        handler = SinkOutputHandler(terminal=None, sinks=[])

        assert handler.prompt_ask(prompt="Upgrade?", choices=["yes", "no"], force_yes=True) == "yes"

    def test_a_default_does_not_answer_a_state_changing_prompt(self):
        """The migration gate prompts with default="update". Honouring a default here starts a
        migration a scripted run never asked for; pressing enter is something only a person does."""
        handler = SinkOutputHandler(terminal=None, sinks=[])

        with pytest.raises(NonInteractiveError) as refusal:
            handler.prompt_ask(
                prompt="How would you like to proceed?",
                choices=["update", "skip"],
                default="update",
                required_flag="'fm migrate BENCH'",
            )

        assert "'fm migrate BENCH'" in refusal.value.suggestions[0]
