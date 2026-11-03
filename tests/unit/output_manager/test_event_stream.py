"""The machine stream names what fm DID, not what it drew.

The old vocabulary was a transcript of the display API: seventeen types named after the methods
that draw a terminal, two describing a spinner redrawing itself and two announcing a prompt that
cannot happen while a machine is reading.
"""

import io
import json

import pytest

from frappe_manager.output_manager.record import OutputRecord
from frappe_manager.output_manager.sinks import EventSink


def _events(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


@pytest.fixture
def stream():
    buf = io.StringIO()
    return EventSink(buf), buf


@pytest.mark.unit
class TestTheContractIsDeclaredFirst:
    def test_the_opening_line_carries_the_schema_version(self, stream):
        sink, buf = stream
        sink.start_run("restart mybench")

        [first] = _events(buf)
        assert first["event"] == "run.started"
        assert first["v"] == EventSink.SCHEMA_VERSION

    def test_the_stream_is_sealed_so_a_reader_knows_nothing_follows(self, stream):
        sink, buf = stream
        sink.finish_run(ok=False, code=7)

        [last] = _events(buf)
        assert last["event"] == "run.finished"
        assert last["ok"] is False
        assert last["code"] == 7

    def test_the_closing_line_carries_a_duration(self):
        """`duration_ms` is a declared field; declaring one and never sending it is worse than
        omitting it, because a reader writes code against the schema."""
        buf = io.StringIO()
        sink = EventSink(buf)

        sink.finish_run(ok=True, code=0)

        [last] = _events(buf)
        assert isinstance(last["duration_ms"], int)


@pytest.mark.unit
class TestAStepIsAScopeNotASentence:
    def test_a_scope_opening_starts_a_step(self, stream):
        sink, buf = stream
        sink.emit(OutputRecord(kind="start", text="Draining RQ workers"))

        assert [e["event"] for e in _events(buf)] == ["step.started"]

    def test_narration_inside_a_scope_emits_nothing(self, stream):
        """A polling wait re-announces its phase every few seconds, with the count changing each
        time. Deriving steps from those sentences would spam a versioned stream, and dedupe would
        not save it -- the text differs on every poll."""
        sink, buf = stream
        sink.emit(OutputRecord(kind="start", text="Waiting for the queue to drain"))
        for remaining in range(50, 0, -1):
            sink.emit(OutputRecord(kind="change_head", text=f"Waiting, {remaining} job(s) left"))
        sink.emit(OutputRecord(kind="stop"))
        sink.finish_run(ok=True, code=0)

        assert [e["event"] for e in _events(buf)] == ["step.started", "step.finished", "run.finished"]

    def test_opening_a_step_closes_the_one_before_it(self, stream):
        sink, buf = stream
        sink.emit(OutputRecord(kind="start", text="first"))
        sink.emit(OutputRecord(kind="start", text="second"))

        assert [(e["event"], e.get("step")) for e in _events(buf)] == [
            ("step.started", "first"),
            ("step.finished", "first"),
            ("step.started", "second"),
        ]

    def test_a_step_that_failed_closes_as_failed(self):
        """In the real path the spinner stops BEFORE the failure is reported -- the error path
        stops it so the message does not land under a spinning line -- so a close decided at stop
        time always read success and the stream told a reader the failing step passed."""
        buf = io.StringIO()
        sink = EventSink(buf)

        sink.emit(OutputRecord(kind="start", text="Working"))
        sink.emit(OutputRecord(kind="stop"))
        sink.emit(OutputRecord(kind="display_error", text="not found"))

        assert [(e["event"], e.get("ok")) for e in _events(buf)] == [
            ("step.started", None),
            ("step.finished", False),
            ("error", None),
        ]

    def test_a_held_close_takes_the_runs_verdict_when_nothing_else_settles_it(self):
        buf = io.StringIO()
        sink = EventSink(buf)

        sink.emit(OutputRecord(kind="start", text="Working"))
        sink.emit(OutputRecord(kind="stop"))
        sink.finish_run(ok=False, code=1)

        assert [(e["event"], e.get("ok")) for e in _events(buf)] == [
            ("step.started", None),
            ("step.finished", False),
            ("run.finished", False),
        ]

    def test_a_result_settles_a_held_close_as_success(self):
        buf = io.StringIO()
        sink = EventSink(buf)

        sink.emit(OutputRecord(kind="start", text="Working"))
        sink.emit(OutputRecord(kind="stop"))
        sink.emit(OutputRecord(kind="data", data=[{"name": "mybench"}]))

        assert [(e["event"], e.get("ok")) for e in _events(buf)] == [
            ("step.started", None),
            ("step.finished", True),
            ("result", None),
        ]


@pytest.mark.unit
class TestCommandsAreJoinableToTheLog:
    def test_a_command_carries_the_id_its_log_line_carries(self, stream):
        sink, buf = stream
        sink.emit(OutputRecord(kind="command.started", fields={"cmd": "a3f1", "argv": "docker ps"}))
        sink.emit(OutputRecord(kind="command.output", fields={"cmd": "a3f1", "stream": "out", "text": "hello"}))
        sink.emit(OutputRecord(kind="command.finished", fields={"cmd": "a3f1", "exit": 0}))

        events = _events(buf)
        assert [e["event"] for e in events] == ["command.started", "command.output", "command.finished"]
        assert {e["cmd"] for e in events} == {"a3f1"}

    def test_the_spinner_and_prompt_types_are_gone(self, stream):
        """A redraw and a prompt mean nothing to a reader that is not a person."""
        sink, buf = stream
        for kind in ("update_live", "update_head", "prompt_ask", "prompt_fuzzy", "debug", "print"):
            sink.emit(OutputRecord(kind=kind, text="ignored"))

        assert _events(buf) == []

    def test_a_result_rides_the_stream(self, stream):
        sink, buf = stream
        sink.emit(OutputRecord(kind="data", data=[{"name": "mybench"}]))

        [event] = _events(buf)
        assert event["event"] == "result"
        assert event["data"] == [{"name": "mybench"}]

    def test_the_sink_declares_what_it_consumes(self):
        """The handler used to find this sink by probing for a private method, which breaks the
        moment another sink grows one."""
        assert EventSink.consumes_command_events is True


@pytest.mark.unit
class TestTheSurfacesDoNotDriftApart:
    def test_a_menu_choice_is_recorded_like_a_typed_answer(self):
        """`prompt_ask` always recorded its answer and `prompt_fuzzy` did not, so the file knew
        every typed response except which bench was picked from a menu."""
        from unittest.mock import MagicMock

        from frappe_manager.output_manager.sink_handler import SinkOutputHandler

        terminal = MagicMock()
        terminal.prompt_fuzzy.return_value = "mybench.localhost"
        recorded: list[str] = []

        class _Recorder:
            def emit(self, record):
                recorded.append(record.text)

        handler = SinkOutputHandler(terminal=terminal, sinks=[_Recorder()])
        handler.prompt_fuzzy(prompt="Select bench", choices=["a", "b"])

        assert any("FUZZY_PROMPT_RESPONSE: mybench.localhost" in line for line in recorded)

    def test_a_sink_that_stops_accepting_events_is_recorded_once(self, monkeypatch):
        """One bad sink drops every command event for the rest of a run while the file keeps all
        of them. Said ONCE, not per line: this helper runs per line of output."""
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import get_global_output_handler, set_global_output_handler
        from frappe_manager.utils import subprocess as sp

        monkeypatch.setattr(sp, "_command_event_failed", False)
        logger = MagicMock()
        monkeypatch.setattr("frappe_manager.logger.get_logger", lambda **_kw: logger)

        # The handler INSTANCE is swapped, never the getter: the project's testing rules say so,
        # and the autouse fixture installs a real one to put back.
        previous = get_global_output_handler()
        broken = MagicMock()
        broken.emit_command_event.side_effect = RuntimeError("sink is gone")
        set_global_output_handler(broken)
        try:
            for _ in range(5):
                sp._command_event("command.output", cmd="a3f1", text="x")
        finally:
            set_global_output_handler(previous)

        assert logger.warning.call_count == 1
        assert "incomplete" in str(logger.warning.call_args.args[0])
