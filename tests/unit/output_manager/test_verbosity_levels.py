"""The two verbosity levels, and the reason the handler carries a level rather than a flag."""


import pytest

from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.sink_handler import SinkOutputHandler


class _Recorder:
    """Records the kind of every record that reaches a sink."""

    def __init__(self, into: list[str]) -> None:
        self.into = into

    def emit(self, record) -> None:
        self.into.append(record.kind)


@pytest.mark.unit
class TestVerbosityReachesTheRenderer:
    def test_the_level_can_be_set_after_construction(self):
        """The handler is built before the command line is parsed, so -v can only arrive later."""
        handler = RichOutputHandler()

        assert handler.verbosity == 0
        handler.set_verbosity(2)
        assert handler.verbosity == 2

    def test_verbose_reads_the_level_rather_than_a_stored_flag(self):
        """Anything still asking `handler.verbose` must follow the level, not a copy taken at birth."""
        handler = RichOutputHandler()
        handler.set_verbosity(1)

        assert handler.verbose is True

        handler.set_verbosity(0)

        assert handler.verbose is False


@pytest.mark.unit
class TestEffectsAndReasoningSplit:
    def test_default_shows_neither(self, capsys):
        handler = RichOutputHandler()

        handler.info("changed a thing")
        handler.debug("considered a thing")

        assert capsys.readouterr().err == ""

    def test_v_shows_effects_only(self, capsys):
        handler = RichOutputHandler()
        handler.set_verbosity(1)

        handler.info("changed a thing")
        handler.debug("considered a thing")

        err = capsys.readouterr().err
        assert "changed a thing" in err
        assert "considered a thing" not in err

    def test_vv_adds_reasoning(self, capsys):
        handler = RichOutputHandler()
        handler.set_verbosity(2)

        handler.info("changed a thing")
        handler.debug("considered a thing")

        err = capsys.readouterr().err
        assert "changed a thing" in err
        assert "considered a thing" in err

    def test_the_sinks_keep_everything_whatever_the_level(self):
        """-v governs the TERMINAL. The file log keeps reasoning at every level, which is the
        point of the split: a failure is diagnosed from the log of the run that already happened,
        not by asking the operator to reproduce it with -v."""
        recorded: list[str] = []
        handler = SinkOutputHandler(terminal=None, sinks=[_Recorder(recorded)])
        handler.set_verbosity(0)

        handler.info("changed a thing")
        handler.debug("considered a thing")

        assert recorded == ["info", "debug"]


@pytest.mark.unit
def test_asking_for_detail_does_not_switch_off_the_command_stream():
    """-v used to be the one flag that could make fm show LESS: the live docker output.

    The suppression was inert only because -v never reached the renderer, so making the flag
    work would have turned it on in the wrong direction.
    """
    handler = RichOutputHandler()
    handler._interactive = True
    handler._spinner_active = True

    handler.set_verbosity(2)

    assert handler.should_stream_docker is True
