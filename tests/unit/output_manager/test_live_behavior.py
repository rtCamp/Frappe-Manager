"""Live output contract.

Defends: Ctrl-C propagates out of streaming (was silently swallowed),
non-interactive heads deduplicate consecutive repeats and suppress the
"Working" placeholder, change_head honors its style parameter (was
accepted and ignored), start() paints the new head on the terminal
immediately instead of waiting for an auto-refresh tick, and stop()
really clears the spinner-active state (so docker streaming and the
live-pause helper both see "no spinner running").
"""

from io import StringIO
from unittest.mock import MagicMock

import pytest
from rich.console import Console
from rich.live import Live

from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.theme import build_theme


def _handler(*, interactive: bool) -> RichOutputHandler:
    handler = RichOutputHandler()
    handler._interactive = interactive  # direct: set_interactive_mode takes a NON-interactive flag
    handler.stderr = Console(file=StringIO(), theme=build_theme(), force_terminal=interactive)
    return handler


def test_noninteractive_heads_deduplicate_and_suppress_working():
    handler = _handler(interactive=False)
    handler.change_head("Doing thing")
    handler.change_head("Doing thing")  # consecutive repeat: silent
    handler.change_head("Working")  # placeholder: always silent
    handler.change_head("Next thing")
    out = handler.stderr.file.getvalue()
    assert out.count("Doing thing") == 1
    assert "Working" not in out
    assert out.count("Next thing") == 1


def test_change_head_honors_style_param():
    handler = _handler(interactive=True)
    handler.live = MagicMock()
    handler.spinner = MagicMock()
    handler.change_head("Styled", style="fm.warn")
    text_arg = handler.spinner.update.call_args.kwargs["text"]
    assert text_arg.style == "fm.warn"


def test_live_lines_propagates_keyboard_interrupt():
    handler = _handler(interactive=True)
    handler.live = MagicMock()

    def stream():
        yield ("stdout", b"line1")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        handler.live_lines(stream())


def _mock_inquirer(monkeypatch, answer: str = "x"):
    from types import SimpleNamespace

    from InquirerPy import inquirer

    stub = lambda **kw: SimpleNamespace(execute=lambda: answer)  # noqa: E731
    monkeypatch.setattr(inquirer, "text", stub)
    monkeypatch.setattr(inquirer, "select", stub)


def test_prompt_with_no_spinner_does_not_leak_one(monkeypatch):
    handler = _handler(interactive=True)
    handler.live = MagicMock()
    _mock_inquirer(monkeypatch)
    assert not handler.is_spinner_active
    assert handler.prompt_ask("question?") == "x"
    assert not handler.is_spinner_active  # resume is conditional: nothing born
    handler.live.stop.assert_not_called()  # and nothing paused


def test_prompt_resumes_active_spinner_with_original_text(monkeypatch):
    handler = _handler(interactive=True)
    handler.live = MagicMock()
    _mock_inquirer(monkeypatch)
    handler.start("Deploying bench")
    assert handler.prompt_ask("question?") == "x"
    assert handler.is_spinner_active
    assert handler._current_text == "Deploying bench"  # not "Working"


@pytest.mark.timeout(15)
def test_start_paints_the_new_head_immediately(monkeypatch):
    # auto_refresh disabled == there IS no next tick, so whatever reaches the
    # terminal here is exactly what start() itself painted. A non-refreshing
    # update would leave the user watching a spinner with no phase text.
    monkeypatch.setenv("TERM", "xterm-256color")  # rich skips live rendering on a dumb terminal
    handler = _handler(interactive=True)
    console = Console(file=StringIO(), theme=build_theme(), force_terminal=True, width=80)
    handler.live = Live(handler.spinner, console=console, transient=True, auto_refresh=False)
    try:
        handler.start("Deploying bench")
        painted = console.file.getvalue()
    finally:
        handler.live.stop()
    assert "Deploying bench" in painted


@pytest.mark.timeout(15)
def test_stop_clears_spinner_state_so_later_output_is_not_treated_as_live():
    handler = _handler(interactive=True)
    handler.live = MagicMock()

    handler.start("Deploying bench")
    assert handler.is_spinner_active
    assert handler.should_stream_docker  # a running spinner owns the terminal

    handler.stop()

    assert not handler.is_spinner_active
    assert not handler.should_stream_docker  # ... a stopped one must not

    handler.live.stop.reset_mock()
    handler.print_data("payload")  # routed through _pause_live
    handler.live.stop.assert_not_called()  # nothing to pause: the spinner is gone
    assert "payload" in handler.stderr.file.getvalue()
