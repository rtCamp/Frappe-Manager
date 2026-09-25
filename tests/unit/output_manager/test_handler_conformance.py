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
from frappe_manager.output_manager.json_output import JSONOutputHandler
from frappe_manager.output_manager.logging_output import LoggingOutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.silent_output import SilentOutputHandler


def _handlers() -> dict[str, OutputHandler]:
    return {
        "rich": RichOutputHandler(),
        "json": JSONOutputHandler(stream=io.StringIO()),
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
    def test_exit_raises_and_never_returns(self, name):
        """Every exit path must terminate the command, whichever handler is installed."""
        output = _handlers()[name]

        with pytest.raises(typer.Exit) as exc:
            output.exit("fatal")

        assert exc.value.exit_code == 1

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
class TestTheLoggingWrapperHasNoHoles:
    def test_every_contract_method_is_reachable_through_the_wrapper(self):
        """A method the wrapper forgets to forward is invisible through it: `emit_exit` was, and
        main.py had to unwrap `handler.delegate` to reach it."""
        delegate = MagicMock()
        delegate.verbose = False
        wrapper = LoggingOutputHandler(delegate)

        missing = [
            name
            for name in dir(OutputHandler)
            if not name.startswith("_") and not hasattr(wrapper, name)
        ]

        assert missing == []

    def test_a_method_only_the_delegate_has_is_still_reachable(self):
        """JSONOutputHandler.emit_exit is not on the contract, and the wrapper must not hide it."""
        delegate = JSONOutputHandler(stream=io.StringIO())
        wrapper = LoggingOutputHandler(delegate)

        assert callable(wrapper.emit_exit)
