"""The destinations a record goes to. Each decides what the record means for its own medium."""

import logging
from typing import Any, Protocol

from frappe_manager.output_manager.record import MASK, OutputRecord, is_secret_label, mask_value


class Sink(Protocol):
    def emit(self, record: OutputRecord) -> None: ...


_LEVELS = {
    "warning": logging.WARNING,
    "display_error": logging.ERROR,
    "error": logging.ERROR,
    "debug": logging.DEBUG,
}


class FileSink:
    """fm.log. Masks by field, which is only possible because it receives fields.

    The file is what an operator pastes into a bug report, so a credential that survives into it
    is reachable by everyone who reads the issue -- unlike the terminal, which is the operator's
    own screen and shows the real value on purpose.
    """

    def __init__(self, log_prefix: str = "[OUTPUT]") -> None:
        from frappe_manager.logger import get_logger

        self.logger = get_logger(component="output")
        self.log_prefix = log_prefix
        self._last_head: str | None = None

    def emit(self, record: OutputRecord) -> None:
        if record.kind == "change_head" and record.text == self._last_head:
            return
        if record.kind == "change_head":
            self._last_head = record.text

        body = self._render(record)
        if not body:
            return
        self.logger.log(_LEVELS.get(record.kind, logging.INFO), f"{self.log_prefix} {body}")

    def _render(self, record: OutputRecord) -> str:
        from frappe_manager.utils.helpers import redact_secrets_in_text

        if record.kind == "data":
            return f"DATA: {self._render_data(record.data)}"
        if record.kind == "start":
            return f"START: {redact_secrets_in_text(record.text)}"
        if record.kind == "change_head":
            return f"CHANGE_HEAD: {redact_secrets_in_text(record.text)}"
        if record.kind == "stop":
            return "STOP"
        return redact_secrets_in_text(record.text)

    def _render_data(self, data: Any) -> str:
        """Render a card here rather than reusing the terminal's rendering of it.

        Masking happens on the way in, while the card still holds (label, value) rows: once it is
        a line of text there is no field left to recognise.
        """
        data = _masked(data)
        if isinstance(data, str):
            return data
        if hasattr(data, "__rich__") or hasattr(data, "__rich_console__"):
            try:
                from rich.console import Console

                from frappe_manager.output_manager.console_singleton import _default_theme

                console = Console(width=120, no_color=True, force_terminal=False, theme=_default_theme())
                with console.capture() as capture:
                    console.print(data)
            except Exception:  # logging must never break the command
                return str(data)
            else:
                text = capture.get().rstrip()
                return f"\n{text}" if "\n" in text else text
        return str(data)


def _mask_fact(label: str, value: str) -> str:
    return MASK if is_secret_label(label) else mask_value(value)


def _masked(data: Any) -> Any:
    """A card with its credentials replaced; anything else untouched."""
    from frappe_manager.output_manager.railcard import Card

    return data.masked(_mask_fact) if isinstance(data, Card) else data


class EventSink:
    """One JSON object per line on stdout, sealed by an exit event."""

    def __init__(self, stream: Any) -> None:
        from frappe_manager.output_manager.json_output import JSONOutputHandler

        self.emitter = JSONOutputHandler(stream=stream)
        self.emitter.set_verbosity(2)

    def emit(self, record: OutputRecord) -> None:
        if record.kind == "data":
            self.emitter.print_data(_masked(record.data))
            return
        method = getattr(self.emitter, _EVENT_METHOD.get(record.kind, "print"), None)
        if method is None:
            return
        if record.kind == "change_head":
            method(record.text, record.fields.get("style"))
        elif record.kind == "stop":
            method()
        else:
            method(record.text)


_EVENT_METHOD = {
    "print": "print",
    "info": "info",
    "debug": "debug",
    "warning": "warning",
    "display_error": "display_error",
    "start": "start",
    "change_head": "change_head",
    "stop": "stop",
    "relay": "relay",
    "data_raw": "data_raw",
}
