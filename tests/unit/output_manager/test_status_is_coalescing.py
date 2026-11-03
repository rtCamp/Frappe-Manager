"""Status is a coalescing channel in the file: last write wins, so a repeat is not a new fact.

On a terminal the spinner head overwrites one line in place. The file treated every call as an
appendable line, so a poll loop -- `fm update`'s queue drain re-states the same sentence every few
seconds while it waits -- produced one line per poll for one unchanged state.

The machine stream solves this differently and is covered in test_event_stream: status is
narration there, and narration is not an event at all.
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.output_manager.record import OutputRecord
from frappe_manager.output_manager.sinks import FileSink


def _head_lines(logger: MagicMock) -> list[str]:
    # FileSink writes through `logger.log(level, message)`, so the text is the SECOND argument.
    return [str(call.args[1]) for call in logger.log.call_args_list if "CHANGE_HEAD" in str(call.args[1])]


@pytest.mark.unit
class TestFileLogCoalescesStatus:
    def test_a_repeated_status_is_recorded_once(self):
        sink = FileSink()
        sink.logger = MagicMock()

        for _ in range(4):
            sink.emit(OutputRecord(kind="change_head", text="Waiting for the queue to drain"))

        assert len(_head_lines(sink.logger)) == 1

    def test_a_changed_status_is_recorded_again(self):
        """Coalescing must not swallow progress: the point of the channel is the latest state."""
        sink = FileSink()
        sink.logger = MagicMock()

        sink.emit(OutputRecord(kind="change_head", text="3 job(s) left"))
        sink.emit(OutputRecord(kind="change_head", text="3 job(s) left"))
        sink.emit(OutputRecord(kind="change_head", text="2 job(s) left"))

        assert len(_head_lines(sink.logger)) == 2
