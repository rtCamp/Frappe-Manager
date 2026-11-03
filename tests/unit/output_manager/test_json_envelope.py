"""One shape for a `--json` answer, succeeding or failing.

A caller that asked for structured output used to get a bare array when it worked and English on
stderr when it did not -- and the failure is the case a program most needs to read.
"""

import json
import sys
from unittest.mock import MagicMock

import pytest

from frappe_manager.commands.gating import asked_for_json_result
from frappe_manager.main import _emit_json_error
from frappe_manager.output_manager.envelope import json_envelope, json_error_envelope
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.sink_handler import SinkOutputHandler


@pytest.mark.unit
class TestBothAnswersShareAShape:
    def test_success_and_failure_carry_the_same_keys(self):
        success = json_envelope([{"name": "mybench"}])
        failure = json_error_envelope("it broke")

        assert {"v", "ok", "result"} <= success.keys()
        assert {"v", "ok", "result"} <= failure.keys()
        assert success["ok"] is True
        assert failure["ok"] is False

    def test_a_result_is_emitted_inside_the_envelope(self, capsys):
        handler = RichOutputHandler()
        handler.set_json_results()

        handler.print_data([{"name": "mybench"}])

        assert json.loads(capsys.readouterr().out) == {"v": 1, "ok": True, "result": [{"name": "mybench"}]}

    def test_without_the_flag_the_data_is_not_wrapped(self):
        """`--events json` and internal data channels must not acquire an envelope they never had."""
        handler = RichOutputHandler()

        assert handler.wants_structured_data is False


@pytest.mark.unit
class TestTheFailureIsMachineReadable:
    def _emitted(self, error: Exception) -> dict:
        handler = SinkOutputHandler(terminal=RichOutputHandler())
        handler.set_json_results()
        printed: list[str] = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("builtins.print", lambda *a, **k: printed.append(a[0]))
            assert _emit_json_error(error, handler) is True
        return json.loads(printed[0])

    def test_the_reason_and_the_next_command_are_fields(self):
        error = RuntimeError("Bench not found")
        error.suggestions = ["Run 'fm list' to see the benches on this host"]

        envelope = self._emitted(error)

        assert envelope["ok"] is False
        assert envelope["error"] == "Bench not found"
        assert envelope["next"] == ["Run 'fm list' to see the benches on this host"]

    def test_markup_never_reaches_the_parser(self):
        """The message was written for a terminal; `[bold]` is not part of the reason."""
        envelope = self._emitted(RuntimeError("[fm.info][bold]nope :[/bold][/fm.info] Bench not found"))

        assert envelope["error"] == "nope : Bench not found"

    def test_nothing_is_emitted_when_json_was_not_asked_for(self):
        handler = SinkOutputHandler(terminal=RichOutputHandler())

        assert _emit_json_error(RuntimeError("boom"), handler) is False

    def test_the_event_stream_is_left_alone(self):
        """Its handler renders its own error event; a pretty document would corrupt the JSONL."""
        handler = SinkOutputHandler(terminal=None)

        assert _emit_json_error(RuntimeError("boom"), handler) is False


@pytest.mark.unit
class TestWhoseFlagItIs:
    def _asked(self, argv: list[str], command: str) -> bool:
        ctx = MagicMock()
        ctx.invoked_subcommand = command
        original, sys.argv = sys.argv, argv
        try:
            return asked_for_json_result(ctx)
        finally:
            sys.argv = original

    def test_fms_own_flag_counts(self):
        assert self._asked(["fm", "info", "mybench", "--json"], "info") is True

    def test_a_flag_past_the_separator_belongs_to_the_child(self):
        assert self._asked(["fm", "compose", "b", "--", "ps", "--json"], "compose") is False

    def test_a_passthrough_command_never_claims_it(self):
        """`fm shell bench ... --json` sends that flag to bench, not to fm."""
        assert self._asked(["fm", "shell", "mybench", "--json"], "shell") is False
