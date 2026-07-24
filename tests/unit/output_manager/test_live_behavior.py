"""Live output contract.

Defends: Ctrl-C propagates out of streaming (was silently swallowed),
non-interactive heads deduplicate consecutive repeats and suppress the
"Working" placeholder, and change_head honors its style parameter (was
accepted and ignored).
"""

from io import StringIO
from unittest.mock import MagicMock

import pytest
from rich.console import Console

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
