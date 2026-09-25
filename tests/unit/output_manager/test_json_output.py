"""
Tests for JSONOutputHandler.

Tests the JSON event capture output handler to ensure all events
are properly captured and can be serialized.
"""

import json

import pytest

from frappe_manager.output_manager.json_output import JSONOutputHandler, OutputEvent


class TestOutputEvent:
    """Tests for the OutputEvent class."""

    def test_create_output_event(self):
        """OutputEvent can be created with type and data."""
        event = OutputEvent("test_event", {"key": "value"})

        assert event.event_type == "test_event"
        assert event.data == {"key": "value"}

    def test_output_event_to_dict(self):
        """OutputEvent converts to a dictionary carrying the event, its data, and a
        UTC timestamp (the envelope streamed by `fm --json`)."""
        event = OutputEvent("print", {"text": "Hello", "emoji": ":zap:"})

        result = event.to_dict()

        assert result["event_type"] == "print"
        assert result["data"] == {"text": "Hello", "emoji": ":zap:"}
        assert result["ts"].endswith("+00:00")

    def test_output_event_to_json(self):
        """OutputEvent can be converted to JSON string."""
        event = OutputEvent("error", {"text": "Error occurred"})

        json_str = event.to_json()
        parsed = json.loads(json_str)

        assert parsed["event_type"] == "error"
        assert parsed["data"]["text"] == "Error occurred"

    def test_to_json_survives_non_serializable_payloads(self):
        """print_data carries arbitrary values (paths, rich renderables); a machine-output
        line must never crash the command that produced it, so to_json stringifies them."""
        from pathlib import Path

        event = OutputEvent("print_data", {"data": Path("/tmp/x"), "kwargs": {}})

        parsed = json.loads(event.to_json())

        assert parsed["data"]["data"] == "/tmp/x"


class TestStreamingMode:
    """`fm --json`: every event is written to the stream as one JSON line, as it happens."""

    def test_each_event_is_streamed_as_one_json_line_immediately(self):
        import io

        buf = io.StringIO()
        handler = JSONOutputHandler(stream=buf)

        handler.start("Working")
        first = buf.getvalue()
        handler.print("hello")
        handler.stop()

        # The first line was written BEFORE later events: streaming, not a dump at exit.
        assert json.loads(first.strip())["event_type"] == "start"
        lines = [json.loads(ln) for ln in buf.getvalue().splitlines()]
        assert [e["event_type"] for e in lines] == ["start", "print", "stop"]
        assert lines[1]["data"]["text"] == "hello"

    def test_every_event_carries_a_timestamp(self):
        import io

        buf = io.StringIO()
        handler = JSONOutputHandler(stream=buf)
        handler.print("x")

        line = json.loads(buf.getvalue().strip())
        assert "ts" in line and line["ts"].endswith("+00:00")

    def test_emit_exit_is_the_terminal_line_and_seals_the_stream(self):
        """A consumer tailing the JSONL must be able to treat 'exit' as end-of-run:
        the atexit cleanup's trailing stop() must not write past it."""
        import io

        buf = io.StringIO()
        handler = JSONOutputHandler(stream=buf)
        handler.start("Working")
        handler.emit_exit(ok=True, code=0)
        handler.stop()  # atexit cleanup fires this after the exit event
        handler.print("straggler")

        lines = [json.loads(ln) for ln in buf.getvalue().splitlines()]
        assert [e["event_type"] for e in lines] == ["start", "exit"]
        assert lines[-1]["data"] == {"ok": True, "code": 0}


class TestJSONOutputHandlerBasics:
    """Tests for basic JSONOutputHandler functionality."""

    def test_initialization(self):
        """JSONOutputHandler initializes with empty event list."""
        handler = JSONOutputHandler()

        assert handler.events == []
        assert handler._current_head is None

    def test_start_captures_event(self):
        """start() captures a start event."""
        handler = JSONOutputHandler()

        handler.start("Starting operation")

        assert len(handler.events) == 1
        event = handler.events[0]
        assert event.event_type == "start"
        assert event.data["text"] == "Starting operation"

    def test_stop_captures_event(self):
        """stop() captures a stop event."""
        handler = JSONOutputHandler()
        handler.start("Test")

        handler.stop()

        assert len(handler.events) == 2
        event = handler.events[1]
        assert event.event_type == "stop"

    def test_print_captures_event(self):
        """print() captures a print event with all parameters."""
        handler = JSONOutputHandler()

        handler.print("Test message", emoji_code=":rocket:", prefix="PREFIX")

        assert len(handler.events) == 1
        event = handler.events[0]
        assert event.event_type == "print"
        assert event.data["text"] == "Test message"
        assert event.data["emoji_code"] == ":rocket:"
        assert event.data["prefix"] == "PREFIX"

    def test_display_error_captures_event(self):
        """display_error() captures an error event without raising."""
        handler = JSONOutputHandler()

        handler.display_error("Error message", emoji_code=":no_entry:")

        assert len(handler.events) == 1
        event = handler.events[0]
        assert event.event_type == "display_error"
        assert event.data["text"] == "Error message"
        assert event.data["emoji_code"] == ":no_entry:"

    def test_error_with_exception_raises(self):
        """error() with exception raises after capturing."""
        handler = JSONOutputHandler()

        with pytest.raises(ValueError, match="Test error"):
            handler.error("Error occurred", exception=ValueError("Test error"))

        # Event should still be captured
        assert len(handler.events) == 1
        event = handler.events[0]
        assert event.event_type == "error"
        assert event.data["exception"] == "Test error"
        assert event.data["exception_type"] == "ValueError"

    def test_warning_captures_event(self):
        """warning() captures a warning event."""
        handler = JSONOutputHandler()

        handler.warning("Warning message")

        assert len(handler.events) == 1
        event = handler.events[0]
        assert event.event_type == "warning"
        assert event.data["text"] == "Warning message"


class TestJSONOutputHandlerHeadOperations:
    """Tests for head update operations."""

    def test_change_head_captures_event(self):
        """change_head() captures event with previous and new text."""
        handler = JSONOutputHandler()
        handler.start("Initial")

        handler.change_head("Updated text", style="bold")

        assert len(handler.events) == 2
        event = handler.events[1]
        assert event.event_type == "change_head"
        assert event.data["text"] == "Updated text"
        assert event.data["previous"] == "Initial"
        assert event.data["style"] == "bold"

    def test_update_head_captures_event(self):
        """update_head() captures event."""
        handler = JSONOutputHandler()
        handler.start("Initial")

        handler.update_head("New head")

        assert len(handler.events) == 2
        event = handler.events[1]
        assert event.event_type == "update_head"
        assert event.data["text"] == "New head"
        assert event.data["previous"] == "Initial"


class TestJSONOutputHandlerAdvancedOperations:
    """Tests for advanced output operations."""

    def test_live_lines_emits_one_event_per_line(self):
        """One event per line, as it arrives.

        It used to buffer the whole stream into a single event emitted at the end, so a hung
        docker command streamed continuously for a human and produced nothing for a machine.
        """
        handler = JSONOutputHandler()

        data = iter(
            [
                ("stdout", b"Line 1\n"),
                ("stderr", b"Error 1\n"),
                ("stdout", b"Line 2\n"),
            ],
        )

        handler.live_lines(data, stdout=True, stderr=True, lines=4)

        assert [(e.event_type, e.data["stream"], e.data["text"]) for e in handler.events] == [
            ("relay", "stdout", "Line 1\n"),
            ("relay", "stderr", "Error 1\n"),
            ("relay", "stdout", "Line 2\n"),
        ]

    def test_live_lines_respects_stdout_filter(self):
        handler = JSONOutputHandler()

        data = iter([("stdout", b"Line 1\n"), ("stderr", b"Error 1\n")])

        handler.live_lines(data, stdout=False, stderr=True)

        assert [e.data["stream"] for e in handler.events] == ["stderr"]

    def test_live_lines_applies_line_filters(self):
        """The filters carry the caller's noise knowledge (docker's progress bars). They were
        accepted and ignored here, so noise filtered off the screen stayed in the machine stream."""
        handler = JSONOutputHandler()

        data = iter([("stdout", b"real line\n"), ("stdout", b"Downloading 45%\n")])

        handler.live_lines(data, line_filters=["downloading"])

        assert [e.data["text"] for e in handler.events] == ["real line\n"]

    def test_live_lines_stops_on_string(self):
        handler = JSONOutputHandler()

        data = iter(
            [
                ("stdout", b"Line 1\n"),
                ("stdout", b"STOP HERE\n"),
                ("stdout", b"Line 3\n"),
            ],
        )

        handler.live_lines(data, stop_string="STOP")

        assert [e.data["text"] for e in handler.events] == ["Line 1\n", "STOP HERE\n"]

    def test_update_live_captures_event(self):
        """update_live() captures renderable content."""
        handler = JSONOutputHandler()

        handler.update_live(renderable="Some content", padding=(1, 2, 3, 4))

        assert len(handler.events) == 1
        event = handler.events[0]
        assert event.event_type == "update_live"
        assert event.data["renderable"] == "Some content"
        assert event.data["padding"] == (1, 2, 3, 4)

    def test_prompt_ask_returns_default_when_provided(self):
        """prompt_ask() returns default value when provided and captures event."""
        handler = JSONOutputHandler()

        result = handler.prompt_ask(prompt="Enter value", default="test")

        assert result == "test"
        assert len(handler.events) == 1
        event = handler.events[0]
        assert event.event_type == "prompt_ask"
        assert event.data["prompt"] == "Enter value"
        assert event.data["default"] == "test"


class TestJSONOutputHandlerEventRetrieval:
    """Tests for event retrieval methods."""

    def test_get_events_returns_dict_list(self):
        """get_events() returns list of event dictionaries."""
        handler = JSONOutputHandler()
        handler.start("Test")
        handler.print("Message")
        handler.stop()

        events = handler.get_events()

        assert isinstance(events, list)
        assert len(events) == 3
        assert all(isinstance(e, dict) for e in events)
        assert events[0]["event_type"] == "start"
        assert events[1]["event_type"] == "print"
        assert events[2]["event_type"] == "stop"


class TestJSONOutputHandlerEventSequencing:
    """Tests for complex event sequences."""

    def test_complete_operation_sequence(self):
        """Test a complete operation from start to finish."""
        handler = JSONOutputHandler()

        handler.start("Starting operation")
        handler.change_head("Processing")
        handler.print("Step 1 complete")
        handler.warning("Minor issue detected")
        handler.change_head("Finalizing")
        handler.print("Done")
        handler.stop()

        events = handler.get_events()
        assert len(events) == 7

        event_types = [e["event_type"] for e in events]
        assert event_types == ["start", "change_head", "print", "warning", "change_head", "print", "stop"]

    def test_multiple_operations(self):
        """A second operation's events append after the first's stop."""
        handler = JSONOutputHandler()

        handler.start("Operation 1")
        handler.stop()
        handler.start("Operation 2")
        handler.stop()

        events = handler.get_events()
        assert [e["event_type"] for e in events] == ["start", "stop", "start", "stop"]
        assert events[2]["data"]["text"] == "Operation 2"
