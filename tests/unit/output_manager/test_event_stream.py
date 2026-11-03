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

        assert [e["event"] for e in _events(buf)] == ["step.started", "step.finished"]

    def test_opening_a_step_closes_the_one_before_it(self, stream):
        sink, buf = stream
        sink.emit(OutputRecord(kind="start", text="first"))
        sink.emit(OutputRecord(kind="start", text="second"))

        assert [(e["event"], e.get("step")) for e in _events(buf)] == [
            ("step.started", "first"),
            ("step.finished", "first"),
            ("step.started", "second"),
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
