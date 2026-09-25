from contextlib import contextmanager

from frappe_manager.output_manager.base import OutputHandler


@contextmanager
def spinner(output: OutputHandler, text: str):
    output.start(text)
    try:
        yield output
    except KeyboardInterrupt:
        output.stop()
        output.print("Operation cancelled by user", emoji_code=":no_entry:")
        raise
    except Exception:
        output.stop()
        raise
    else:
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


