"""The teardown that releases the terminal before another program takes it over.

Forty-eight assertions check what the five exec sites pass; none checked that the renderer was
stopped first. That teardown is why `fm compose` stopped leaving a hidden cursor behind, and the
sink split rewrites what a renderer is, so the contract needs a test that outlives both.
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.sink_handler import SinkOutputHandler
from frappe_manager.output_manager.sinks import FileSink


@pytest.mark.unit
class TestHandoffReleasesTheTerminal:
    def test_the_spinner_is_down_before_the_body_runs(self):
        terminal = MagicMock(spec=RichOutputHandler)
        handler = SinkOutputHandler(terminal=terminal)

        with handler.handoff():
            assert terminal.stop.call_count == 1

    def test_a_real_renderer_is_no_longer_spinning_inside_the_handoff(self):
        terminal = RichOutputHandler()
        handler = SinkOutputHandler(terminal=terminal)
        handler.start("working")

        with handler.handoff():
            assert handler.is_spinner_active is False

    def test_the_other_sinks_are_not_torn_down(self):
        """Only the terminal is released: the file has nothing to hand over, and silencing it
        would lose the record of what the child was given."""
        terminal = MagicMock(spec=RichOutputHandler)
        file_sink = MagicMock(spec=FileSink)
        handler = SinkOutputHandler(terminal=terminal, sinks=[file_sink])

        with handler.handoff():
            pass

        assert not hasattr(file_sink, "stop") or file_sink.stop.call_count == 0

    def test_a_handler_with_no_terminal_still_yields(self):
        """`--events json` detaches the terminal; an exec under it must not raise on the way out."""
        handler = SinkOutputHandler(terminal=None)

        with handler.handoff():
            pass
