import pytest

from frappe_manager.output_manager import (
    nested_spinner,
    spinner,
    spinner_or_pass,
    temporary_stop,
)
from frappe_manager.output_manager.json_output import JSONOutputHandler


def test_spinner_context_manager_basic():
    output = JSONOutputHandler()

    with spinner(output, "Working"):
        pass

    events = output.get_events()
    assert len(events) == 2
    assert events[0]["event_type"] == "start"
    assert events[0]["data"]["text"] == "Working"
    assert events[1]["event_type"] == "stop"


def test_spinner_context_manager_exception_safety():
    output = JSONOutputHandler()

    with pytest.raises(ValueError), spinner(output, "Working"):
        raise ValueError("Test error")

    events = output.get_events()
    assert events[-1]["event_type"] == "stop"


def test_spinner_context_manager_change_text():
    output = JSONOutputHandler()

    with spinner(output, "Working") as out:
        out.change_head("Still working")
        out.print("Done something")

    events = output.get_events()
    assert events[0]["event_type"] == "start"
    assert events[1]["event_type"] == "change_head"
    assert events[2]["event_type"] == "print"
    assert events[3]["event_type"] == "stop"


def test_spinner_or_pass_enabled():
    output = JSONOutputHandler()

    with spinner_or_pass(output, "Working", enabled=True):
        pass

    events = output.get_events()
    assert len(events) == 2
    assert events[0]["event_type"] == "start"
    assert events[1]["event_type"] == "stop"


def test_spinner_or_pass_disabled():
    output = JSONOutputHandler()

    with spinner_or_pass(output, "Working", enabled=False):
        output.print("Direct message")

    events = output.get_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "print"


def test_temporary_stop():
    output = JSONOutputHandler()

    with spinner(output, "Working"):
        output.print("Task 1")

        with temporary_stop(output):
            output.print("User prompt")

        output.print("Task 2")

    events = output.get_events()
    assert events[0]["event_type"] == "start"
    assert events[1]["event_type"] == "print"
    assert events[2]["event_type"] == "stop"
    assert events[3]["event_type"] == "print"
    assert events[4]["event_type"] == "start"
    assert events[5]["event_type"] == "print"
    assert events[6]["event_type"] == "stop"


def test_keyboard_interrupt_stops_spinner():
    output = JSONOutputHandler()

    with pytest.raises(KeyboardInterrupt), spinner(output, "Working"):
        raise KeyboardInterrupt

    events = output.get_events()
    stop_events = [e for e in events if e["event_type"] == "stop"]
    assert len(stop_events) >= 1


def test_nested_spinner():
    output = JSONOutputHandler()

    with spinner(output, "Outer"):
        output.print("Outer work")

        with nested_spinner(output, "Outer", "Inner"):
            output.print("Inner work")

        output.print("Back to outer")

    events = output.get_events()
    start_events = [e for e in events if e["event_type"] == "start"]
    assert len(start_events) == 3


def test_spinner_state_tracking():
    output = JSONOutputHandler()

    assert not output.is_spinner_active

    with spinner(output, "Working"):
        assert output.is_spinner_active

    assert not output.is_spinner_active
