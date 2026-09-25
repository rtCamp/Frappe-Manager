"""Status is a coalescing channel: last write wins, so a repeat is not a new fact.

On a terminal the spinner head overwrites one line in place. The recorders treated every call as
an appendable event, so a poll loop -- `fm update`'s queue drain re-states the same sentence every
few seconds while it waits -- produced one log line and one JSON event per poll for one unchanged
state.
"""

import io
import json
import logging
from unittest.mock import MagicMock

import pytest

from frappe_manager.output_manager.json_output import JSONOutputHandler
from frappe_manager.output_manager.logging_output import LoggingOutputHandler


def _events(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


@pytest.mark.unit
class TestJSONStreamCoalescesStatus:
    def test_a_repeated_status_is_not_a_second_event(self):
        stream = io.StringIO()
        output = JSONOutputHandler(stream=stream)

        for _ in range(5):
            output.change_head("Waiting for the queue to drain, 3 job(s) left")

        assert len(_events(stream)) == 1

    def test_a_changed_status_still_emits(self):
        """Coalescing must not swallow progress: the point of the channel is the latest state."""
        stream = io.StringIO()
        output = JSONOutputHandler(stream=stream)

        output.change_head("3 job(s) left")
        output.change_head("3 job(s) left")
        output.change_head("2 job(s) left")

        assert [event["data"]["text"] for event in _events(stream)] == ["3 job(s) left", "2 job(s) left"]


@pytest.mark.unit
class TestFileLogCoalescesStatus:
    def test_a_repeated_status_is_logged_once(self):
        delegate = MagicMock()
        delegate.verbose = False
        output = LoggingOutputHandler(delegate)
        output.logger = MagicMock()

        for _ in range(4):
            output.change_head("Waiting for the queue to drain")

        head_logs = [c for c in output.logger.debug.call_args_list if "CHANGE_HEAD" in c.args[0]]
        assert len(head_logs) == 1

    def test_the_delegate_still_sees_every_call(self):
        """The terminal repaints on every call; only the RECORDING is coalesced."""
        delegate = MagicMock()
        delegate.verbose = False
        output = LoggingOutputHandler(delegate)
        output.logger = MagicMock(spec=logging.Logger)

        for _ in range(4):
            output.change_head("Waiting for the queue to drain")

        assert delegate.change_head.call_count == 4
