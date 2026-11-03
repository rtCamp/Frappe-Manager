"""Properties that must NOT change when the handler changes.

Swapping the output handler is supposed to change how output looks, never what the program does.
It did not hold: `error(text, None)` returned in Rich and raised TypeError elsewhere, and a method
the logging wrapper forgot to forward was invisible through it. These pin the semantics that have
to agree, so a future handler cannot drift the way this family already did.
"""

import io
from unittest.mock import MagicMock

import pytest
import typer

from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.silent_output import SilentOutputHandler
from frappe_manager.output_manager.sink_handler import SinkOutputHandler
from frappe_manager.output_manager.sinks import EventSink


def _handlers() -> dict[str, OutputHandler]:
    return {
        "rich": RichOutputHandler(),
        "sink": SinkOutputHandler(terminal=None, sinks=[EventSink(io.StringIO())]),
        "silent": SilentOutputHandler(),
    }


@pytest.mark.unit
class TestEveryHandlerAgreesOnControlFlow:
    @pytest.mark.parametrize("name", sorted(_handlers()))
    def test_error_always_raises(self, name):
        """`error` is documented as always raising; a handler that returns lets a failed command
        carry on. Rich used to, because it guarded the raise behind a falsy check."""
        output = _handlers()[name]

        with pytest.raises(ValueError, match="boom"):
            output.error("failed", ValueError("boom"))

    @pytest.mark.parametrize("name", sorted(_handlers()))
    def test_display_error_reports_without_terminating(self, name):
        """There is no longer an `exit()` on the contract: a command that means to stop raises
        `typer.Exit` itself, at the call site, where the control flow is visible. `display_error`
        REPORTS and returns, and every handler must agree on that."""
        output = _handlers()[name]

        output.display_error("failed")

    @pytest.mark.parametrize("name", sorted(_handlers()))
    def test_live_lines_consumes_the_whole_stream(self, name):
        """Callers read the child's exit code off a generator `live_lines` drives; a handler that
        does not drain it changes control flow, not rendering."""
        output = _handlers()[name]
        drained = []

        def stream():
            for line in (b"one", b"two", b"three"):
                drained.append(line)
                yield "stdout", line

        output.live_lines(stream())

        assert drained == [b"one", b"two", b"three"]

    @pytest.mark.parametrize("name", sorted(_handlers()))
    def test_stop_string_ends_the_stream_everywhere(self, name):
        """One stream, one termination condition, whichever handler renders it."""
        output = _handlers()[name]
        drained = []

        def stream():
            for line in (b"working", b"DONE marker", b"never reached"):
                drained.append(line)
                yield "stdout", line

        output.live_lines(stream(), stop_string="done marker")

        assert b"never reached" not in drained


@pytest.mark.unit
class TestTheLiveHandlerHasNoHoles:
    def test_every_contract_method_is_reachable(self):
        """A missing method is invisible until the one path that calls it runs. `emit_exit` was
        exactly that: the old logging wrapper did not forward it, and main.py had to reach past
        the wrapper to find it."""
        handler = SinkOutputHandler(terminal=None, sinks=[EventSink(io.StringIO())])

        missing = [name for name in dir(OutputHandler) if not name.startswith("_") and not hasattr(handler, name)]

        assert missing == []

    def test_the_exit_event_is_reachable_without_unwrapping(self):
        """`emit_exit` is not on the contract, and main.py calls it on whatever handler is
        installed -- so it must be present directly."""
        handler = SinkOutputHandler(terminal=None, sinks=[EventSink(io.StringIO())])

        assert callable(handler.emit_exit)
