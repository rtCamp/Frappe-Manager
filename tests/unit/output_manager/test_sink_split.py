"""Sinks receive records, and each decides what the record means for its own medium."""

from unittest.mock import MagicMock

import pytest

from frappe_manager.output_manager.railcard import Card
from frappe_manager.output_manager.record import OutputRecord
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.sink_handler import SinkOutputHandler
from frappe_manager.output_manager.sinks import FileSink


def _flatten(renderable) -> str:
    from rich.console import Console

    console = Console(width=200, no_color=True, force_terminal=False)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


class _Recorder:
    def __init__(self) -> None:
        self.records: list[OutputRecord] = []

    def emit(self, record: OutputRecord) -> None:
        self.records.append(record)


@pytest.mark.unit
class TestEveryDestinationSeesTheSameCall:
    def test_one_call_reaches_every_attached_sink(self):
        first, second = _Recorder(), _Recorder()
        handler = SinkOutputHandler(terminal=None, sinks=[first, second])

        handler.print("a thing happened")

        assert [r.text for r in first.records] == ["a thing happened"]
        assert [r.text for r in second.records] == ["a thing happened"]

    def test_a_terminal_and_a_recorder_can_be_attached_at_once(self):
        """The property that could not exist while one handler replaced another: a person
        watching while a machine records."""
        terminal = MagicMock(spec=RichOutputHandler)
        recorder = _Recorder()
        handler = SinkOutputHandler(terminal=terminal, sinks=[recorder])

        handler.print("a thing happened")

        assert terminal.print.call_count == 1
        assert len(recorder.records) == 1

    def test_sinks_receive_fields_not_rendered_text(self):
        recorder = _Recorder()
        handler = SinkOutputHandler(terminal=None, sinks=[recorder])
        card = Card(name="mybench", meta="running").fact("db", "user / hunter2")

        handler.print_data(card)

        [record] = recorder.records
        assert record.kind == "data"
        assert record.data is card


@pytest.mark.unit
class TestTheFileMasksByFieldName:
    def _logged(self, card) -> str:
        sink = FileSink()
        sink.logger = MagicMock()
        sink.emit(OutputRecord(kind="data", data=card))
        return str(sink.logger.log.call_args.args[1])

    def test_a_marked_secret_goes_and_the_rest_of_the_row_stays(self):
        """A card marks its secrets where it builds them; the name beside one is why people read
        the log at all, so only the marked span is replaced."""
        card = Card(name="mybench", meta="running").fact(
            "db", "fm_rel19 [fm.muted]/[/fm.muted] [fm.secret]QPc97k1FEbFFAMtY[/fm.secret]"
        )

        logged = self._logged(card)

        assert "QPc97k1FEbFFAMtY" not in logged
        assert "fm_rel19" in logged
        assert "****" in logged

    def test_a_secret_labelled_row_never_reaches_the_file(self):
        card = Card(name="mybench", meta="running").fact("db password", "QPc97k1FEbFFAMtY")

        assert "QPc97k1FEbFFAMtY" not in self._logged(card)
        assert "****" in self._logged(card)

    def test_an_ordinary_row_is_untouched(self):
        card = Card(name="mybench", meta="running").fact("dir", "/home/frappe/frappe/sites/mybench")

        assert "/home/frappe/frappe/sites/mybench" in self._logged(card)

    def test_the_terminal_still_shows_the_real_value(self):
        """The operator's own screen is not the leak: the file is what gets pasted into an issue."""
        terminal = MagicMock(spec=RichOutputHandler)
        handler = SinkOutputHandler(terminal=terminal, sinks=[])
        card = Card(name="mybench", meta="running").fact("db password", "QPc97k1FEbFFAMtY")

        handler.print_data(card)

        # The terminal gets the card's own drawing, with the value intact; the sinks above got the
        # card. Masking happens on the way to the file, never on the way to the operator.
        drawn = terminal.print_data.call_args.args[0]
        assert drawn is not card
        assert "QPc97k1FEbFFAMtY" in _flatten(drawn)
