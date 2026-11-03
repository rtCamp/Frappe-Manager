"""The spinner and temporary-stop scopes, seen through the machine stream.

A scope is what opens a unit of work, so these are the calls the stream turns into steps; the
narration inside them is not.
"""

import io
import json

import pytest

from frappe_manager.output_manager import (
    spinner,
    temporary_stop,
)
from frappe_manager.output_manager.sink_handler import SinkOutputHandler
from frappe_manager.output_manager.sinks import EventSink


def _handler() -> tuple[SinkOutputHandler, io.StringIO]:
    stream = io.StringIO()
    return SinkOutputHandler(terminal=None, sinks=[EventSink(stream)]), stream


def _events(stream: io.StringIO) -> list[str]:
    return [json.loads(line)["event"] for line in stream.getvalue().splitlines() if line.strip()]


@pytest.mark.unit
def test_a_spinner_scope_is_one_step():
    output, stream = _handler()

    with spinner(output, "Working"):
        pass

    assert _events(stream) == ["step.started", "step.finished"]


@pytest.mark.unit
def test_the_step_is_closed_even_when_the_body_raises():
    """An unclosed step leaves a reader waiting forever for a finish that never comes."""
    output, stream = _handler()

    with pytest.raises(ValueError), spinner(output, "Working"):
        raise ValueError("Test error")

    assert _events(stream)[-1] == "step.finished"


@pytest.mark.unit
def test_a_keyboard_interrupt_still_closes_the_step():
    output, stream = _handler()

    with pytest.raises(KeyboardInterrupt), spinner(output, "Working"):
        raise KeyboardInterrupt

    assert "step.finished" in _events(stream)


@pytest.mark.unit
def test_narration_inside_the_scope_does_not_open_a_step():
    output, stream = _handler()

    with spinner(output, "Working") as out:
        out.change_head("Still working")
        out.print("Done something")

    assert _events(stream) == ["step.started", "step.finished"]


@pytest.mark.unit
def test_temporary_stop_closes_and_reopens_the_step():
    """The scope is suspended so a prompt can own the terminal, and the stream says so rather
    than reporting one long step that was not running throughout."""
    output, stream = _handler()

    with spinner(output, "Working"), temporary_stop(output):
        pass

    assert _events(stream) == ["step.started", "step.finished", "step.started", "step.finished"]


@pytest.mark.unit
def test_spinner_state_tracking():
    output, _ = _handler()

    assert not output.is_spinner_active

    with spinner(output, "Working"):
        assert output.is_spinner_active

    assert not output.is_spinner_active
