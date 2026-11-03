"""Status is a coalescing channel in the log: last write wins, so a repeat is not a new fact.

On a terminal the spinner head overwrites one line in place. The log treated every call as an
appendable event, so a poll loop -- `fm update`'s queue drain re-states the same sentence every
few seconds while it waits -- produced one line per poll for one unchanged state. The machine
stream solves this differently and is covered in test_event_stream: status is narration, and
narration is not an event there at all.
"""

import logging
from unittest.mock import MagicMock

import pytest

from frappe_manager.output_manager.logging_output import LoggingOutputHandler


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
