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
    """One JSON object per line on stdout, naming what fm DID.

    The old vocabulary was a transcript of the display API -- seventeen types named after the
    methods that draw a terminal, two of them describing a spinner redrawing itself and two more
    announcing a prompt that cannot happen while a machine is reading. A reader could tell that
    something changed; to learn what, it had to match English.

    `run` is the correlation id the log already stamps on every line, and `cmd` is the same four
    characters the log line carries, so one run's file and stream join on either.
    """

    SCHEMA_VERSION = 1
    # Declared, not inferred: the handler used to find this sink by probing for a private method,
    # which is the shape-guessing the split exists to remove and breaks the moment another sink
    # grows one.
    consumes_command_events = True

    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self._open_step: str | None = None
        self._started = False

    def _write(self, event: str, **fields: Any) -> None:
        import json

        from frappe_manager.logger.ambient import current_context

        run = current_context().correlation_id
        payload = {"event": event, "run": run[:8] if run else None, **{k: v for k, v in fields.items() if v is not None}}
        print(json.dumps(payload, default=str), file=self.stream, flush=True)

    def start_run(self, argv: str) -> None:
        """The first line declares the contract: fields may be added, none renamed."""
        if self._started:
            return
        self._started = True
        from frappe_manager.utils.helpers import get_current_fm_version

        self._write("run.started", v=self.SCHEMA_VERSION, fm=str(get_current_fm_version()), argv=argv)

    def emit(self, record: OutputRecord) -> None:
        if record.kind == "start":
            # A step is a SCOPE opening -- the 48 places fm starts a unit of work -- never one of
            # the 213 narration calls inside them. Deriving a step name from an English sentence
            # rots the first time somebody rewords one, and a polling wait that re-announces the
            # same phase every few seconds would emit hundreds of identical pairs.
            if self._open_step is not None:
                self._write("step.finished", step=self._open_step, ok=True)
            self._open_step = record.text
            self._write("step.started", step=record.text)
        elif record.kind == "stop":
            if self._open_step is not None:
                self._write("step.finished", step=self._open_step, ok=True)
                self._open_step = None
        elif record.kind == "data":
            self._write("result", data=_masked(record.data))
        elif record.kind == "warning":
            self._write("warning", text=record.text)
        elif record.kind in ("error", "display_error"):
            self._write("error", error=record.text)
        elif record.kind.startswith("command."):
            self._write(record.kind, **record.fields)

    def finish_run(self, ok: bool, code: int, duration_ms: int | None = None) -> None:
        self._write("run.finished", ok=ok, code=code, duration_ms=duration_ms)
