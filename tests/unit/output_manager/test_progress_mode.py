"""`--progress` decides how progress is drawn, and who overrules whom when three controls meet."""

from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager import ProgressMode
from frappe_manager.commands import app_callback
from frappe_manager.output_manager.rich_output import RichOutputHandler


def _handler(mode: str, interactive: bool = True) -> RichOutputHandler:
    handler = RichOutputHandler()
    handler._interactive = interactive
    handler.set_progress_mode(mode)
    return handler


@pytest.mark.unit
class TestTheModeDecidesTheDrawing:
    def test_auto_animates_at_a_terminal(self):
        assert _handler("auto")._animates is True

    def test_auto_does_not_animate_away_from_one(self):
        """The check `auto` keys on already excludes CI, which is why plain-in-CI needs no rule."""
        assert _handler("auto", interactive=False)._animates is False

    def test_plain_never_animates_even_at_a_terminal(self):
        assert _handler("plain")._animates is False

    def test_plain_still_announces_each_step(self, capsys):
        handler = _handler("plain")
        handler.start("doing a thing")

        assert "doing a thing" in capsys.readouterr().err

    def test_none_says_nothing_about_progress(self, capsys):
        handler = _handler("none")
        handler.start("doing a thing")

        assert capsys.readouterr().err == ""

    def test_results_survive_every_mode(self, capsys):
        """Progress is not the result: silencing one must never silence the other."""
        handler = _handler("none")
        handler.print("the thing happened")

        assert "the thing happened" in capsys.readouterr().err


@pytest.mark.unit
class TestPrecedenceBetweenTheThreeControls:
    def _mode(self, **kwargs) -> str:
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "list"
        with patch("frappe_manager.commands.will_print_help", return_value=True):
            app_callback(ctx, version=None, **kwargs)
        return ctx.obj["progress"]

    def test_a_bare_run_leaves_the_judgement_to_auto(self):
        assert self._mode() == "auto"

    def test_non_interactive_implies_plain(self):
        assert self._mode(non_interactive=True) == "plain"

    def test_an_explicit_mode_outranks_that_implication(self):
        """Otherwise a flag the operator typed would be silently overruled by one they did not."""
        assert self._mode(non_interactive=True, progress=ProgressMode.none) == "none"
