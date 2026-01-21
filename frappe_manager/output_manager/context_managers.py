from contextlib import contextmanager
from typing import Optional

from frappe_manager.output_manager.base import OutputHandler


@contextmanager
def spinner(output: OutputHandler, text: str, handle_keyboard_interrupt: bool = True):
    output.start(text)
    try:
        yield output
    except KeyboardInterrupt:
        if handle_keyboard_interrupt:
            output.stop()
            output.print("Operation cancelled by user", emoji_code=":no_entry:")
        raise
    except Exception:
        output.stop()
        raise
    else:
        output.stop()


@contextmanager
def spinner_or_pass(output: OutputHandler, text: str, enabled: bool = True):
    if enabled:
        output.start(text)
    try:
        yield output
    finally:
        if enabled:
            output.stop()


@contextmanager
def temporary_stop(output: OutputHandler):
    was_active = output.is_spinner_active
    current_text = output._current_text

    if was_active:
        output.stop()

    try:
        yield
    finally:
        if was_active and current_text:
            output.start(current_text)


@contextmanager
def nested_spinner(output: OutputHandler, outer_text: str, inner_text: str):
    output.stop()
    output.start(inner_text)
    try:
        yield output
    finally:
        output.stop()
        output.start(outer_text)
