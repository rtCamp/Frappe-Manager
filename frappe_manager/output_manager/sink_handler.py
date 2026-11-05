"""The facade commands call, and the thing that decides nothing about appearance.

Every method builds an `OutputRecord` and hands it to each attached sink. The terminal is one
sink among them, not the thing the others are wrapped around: that inversion is what lets the
file mask by field, lets the stream carry facts rather than a transcript of the display API, and
lets a person watch a run while a machine records it.

The 17-method contract above it does not change. Roughly 1470 call sites do not move.
"""

import traceback
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from typing import Any

from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.output_manager.record import OutputRecord
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.sinks import Sink


class SinkOutputHandler(OutputHandler):
    def __init__(self, terminal: RichOutputHandler | None = None, sinks: Sequence[Sink] = ()) -> None:
        super().__init__()
        # The ONE renderer. Terminal-shaped operations -- the spinner, the live region, prompts,
        # handing the terminal to another program -- belong to it alone, which is the invariant the
        # 26 ad-hoc `or RichOutputHandler()` fallbacks used to break.
        self.terminal = terminal
        self.sinks: list[Sink] = list(sinks)
        self._scope_open = False

    def add_sink(self, sink: Sink) -> None:
        self.sinks.append(sink)

    def set_verbosity(self, level: int) -> None:
        super().set_verbosity(level)
        if self.terminal is not None:
            self.terminal.set_verbosity(level)

    def set_progress_mode(self, mode: str) -> None:
        super().set_progress_mode(mode)
        if self.terminal is not None:
            self.terminal.set_progress_mode(mode)

    def set_result_fields(self, fields: list[str] | None) -> None:
        super().set_result_fields(fields)
        if self.terminal is not None:
            self.terminal.set_result_fields(fields)

    def emit_command_event(self, kind: str, **fields: Any) -> None:
        """A child process starting, speaking or finishing. Sinks only: the terminal already drew
        this through `live_lines`, and drawing it twice is what the split exists to stop."""
        record = OutputRecord(kind=kind, fields=fields)
        for sink in self.sinks:
            if getattr(sink, "consumes_command_events", False):
                sink.emit(record)

    def _emit(self, kind: str, text: str = "", **fields: Any) -> None:
        record = OutputRecord(kind=kind, text=text, fields=fields)
        for sink in self.sinks:
            sink.emit(record)

    def start(self, text: str) -> None:
        self._emit("start", text)
        self._scope_open = True
        if self.terminal is not None:
            self.terminal.start(text)
        super().start(text)

    def change_head(self, text: str, style: str | None = None) -> None:
        self._emit("change_head", text, style=style)
        if self.terminal is not None:
            self.terminal.change_head(text, style)

    def update_head(self, text: str) -> None:
        self._emit("change_head", text)
        if self.terminal is not None:
            self.terminal.update_head(text)

    def stop(self) -> None:
        self._emit("stop")
        self._scope_open = False
        if self.terminal is not None:
            self.terminal.stop()
        super().stop()

    def print(self, text: str, emoji_code: str = ":zap:", prefix: str | None = None, **kwargs) -> None:
        self._emit("print", text, emoji_code=emoji_code, prefix=prefix)
        if self.terminal is not None:
            self.terminal.print(text, emoji_code=emoji_code, prefix=prefix, **kwargs)

    def effect(self, text: str, changed: bool = True, emoji_code: str = ":zap:", **kwargs) -> None:
        self._emit("effect", text, emoji_code=emoji_code, changed=changed)
        if self.terminal is not None:
            self.terminal.effect(text, changed=changed, emoji_code=emoji_code, **kwargs)

    def debug(self, text: str, emoji_code: str = ":bug:", **kwargs) -> None:
        self._emit("debug", text, emoji_code=emoji_code)
        if self.terminal is not None:
            self.terminal.debug(text, emoji_code=emoji_code, **kwargs)

    def info(self, text: str, emoji_code: str = ":information:", **kwargs) -> None:
        self._emit("info", text, emoji_code=emoji_code)
        if self.terminal is not None:
            self.terminal.info(text, emoji_code=emoji_code, **kwargs)

    def display_error(self, text: str, emoji_code: str = ":no_entry:") -> None:
        self._emit("display_error", text, emoji_code=emoji_code)
        if self.terminal is not None:
            self.terminal.display_error(text, emoji_code=emoji_code)

    def error(self, text: str, exception: Exception, emoji_code: str = ":no_entry:") -> None:
        self._emit("error", text, exception=f"{type(exception).__name__}: {exception}")
        from frappe_manager.logger import get_logger

        # Formatted from the exception in hand, NOT from sys.exc_info(): callers reach this with
        # an exception they built rather than one they caught, and reading the ambient one dies
        # inside the error path, replacing the real failure with an AttributeError.
        detail = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
        get_logger(component="output").error(f"[OUTPUT] Exception detail:\n{detail}")
        if self.terminal is not None:
            self.terminal.display_error(text, emoji_code)
        raise exception

    def warning(self, text: str, emoji_code: str = ":warning:") -> None:
        self._emit("warning", text, emoji_code=emoji_code)
        if self.terminal is not None:
            self.terminal.warning(text, emoji_code=emoji_code)

    def live_lines(
        self,
        data: Iterable[tuple[str, bytes]],
        stdout: bool = True,
        stderr: bool = True,
        lines: int = 4,
        padding: tuple[int, int, int, int] = (0, 0, 0, 2),
        stop_string: str | None = None,
        log_prefix: str = "=>",
        line_filters: Sequence[str] | None = None,
    ) -> None:
        self._emit("debug", f"LIVE_LINES: starting (stdout={stdout}, stderr={stderr})")
        if self.terminal is not None:
            self.terminal.live_lines(data, stdout, stderr, lines, padding, stop_string, log_prefix, line_filters)
        else:
            # One stream, one termination condition: a headless run that drains past stop_string
            # changes control flow, not rendering -- the caller reads the child's exit code off
            # this generator and a command that should have stopped keeps going.
            for _source, line in data:
                if stop_string and stop_string.lower() in line.decode(errors="replace").lower():
                    break

    def update_live(self, renderable: Any = None, padding: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        if self.terminal is not None:
            self.terminal.update_live(renderable, padding)

    def prompt_ask(self, prompt: str = "", choices=None, default: str | None = None, force_yes: bool = False, required_flag: str | None = None, **kwargs) -> str:
        if self.terminal is None:
            # `--yes` is read BEFORE refusing: it means "do the thing I typed", so a headless run
            # that passed it has answered. A `default` is NOT taken here, unlike `prompt_fuzzy`
            # below: these prompts gate state changes, and the migration gate's default is
            # "update", so honouring it would start a migration nobody asked for.
            if force_yes:
                return "yes" if not choices or "yes" in choices else choices[0]
            from frappe_manager.exceptions import NonInteractiveError

            raise NonInteractiveError(prompt, [f"Pass {required_flag}"] if required_flag else None)
        self._emit("info", f"PROMPT: {prompt}")
        answer = self.terminal.prompt_ask(
            prompt=prompt, choices=choices, default=default, force_yes=force_yes, required_flag=required_flag, **kwargs
        )
        self._emit("info", "PROMPT_RESPONSE: [hidden]" if "password" in prompt.lower() else f"PROMPT_RESPONSE: {answer}")
        return answer

    def prompt_fuzzy(self, prompt: str, choices: list[str], default: str | None = None, required_flag: str | None = None, **kwargs) -> str:
        if self.terminal is None:
            # A default IS the answer; refusing when one exists turns a runnable headless command
            # into a failure over a question that was already settled.
            if default is not None:
                return default
            from frappe_manager.exceptions import NonInteractiveError

            raise NonInteractiveError(prompt, [f"Pass {required_flag}"] if required_flag else None)
        self._emit("info", f"FUZZY_PROMPT: {prompt} ({len(choices)} choices)")
        answer = self.terminal.prompt_fuzzy(
            prompt=prompt, choices=choices, default=default, required_flag=required_flag, **kwargs
        )
        # `prompt_ask` has always recorded its answer; this one did not, so the file knew every
        # typed response EXCEPT which bench was chosen from a menu -- the one an operator most
        # often cannot remember afterwards.
        self._emit("info", f"FUZZY_PROMPT_RESPONSE: {answer}")
        return answer

    @property
    def should_stream_docker(self) -> bool:
        return self.terminal is not None and self.terminal.should_stream_docker

    @property
    def is_spinner_active(self) -> bool:
        # Tracked here, not read off the terminal: headless there IS no spinner, and answering
        # False made `temporary_stop` decline to resume the scope it had just suspended.
        return self._scope_open

    def print_data(self, data: Any, **kwargs) -> None:
        record = OutputRecord(kind="data", data=data)
        for sink in self.sinks:
            sink.emit(record)
        if self.terminal is not None:
            # Rendered HERE and nowhere earlier: the sinks above need the fields, and rich prints a
            # card's dataclass repr rather than its rows unless it is handed the rendering.
            render = getattr(data, "render", None)
            self.terminal.print_data(render() if callable(render) else data, **kwargs)

    def data_raw(self, text: str) -> None:
        self._emit("data_raw", text)
        if self.terminal is not None:
            self.terminal.data_raw(text)

    def relay(self, text: str, *, stream: str = "stdout") -> None:
        self._emit("relay", text, stream=stream)
        if self.terminal is not None:
            self.terminal.relay(text, stream=stream)

    def set_interactive_mode(self, non_interactive_flag: bool) -> None:
        super().set_interactive_mode(non_interactive_flag)
        if self.terminal is not None:
            self.terminal.set_interactive_mode(non_interactive_flag)

    def is_interactive(self) -> bool:
        if self.terminal is None:
            return False
        return self.terminal.is_interactive()

    def set_json_results(self, enabled: bool = True) -> None:
        if self.terminal is not None:
            self.terminal.set_json_results(enabled)

    @property
    def wants_structured_data(self) -> bool:
        return self.terminal is not None and self.terminal.wants_structured_data

    @contextmanager
    def handoff(self):
        """Release the terminal for a program that takes it over.

        Only the terminal sink is torn down: the file and the stream have nothing to release, and
        stopping them here would lose the record of what the child was handed.
        """
        self.stop()
        yield

    def emit_exit(self, ok: bool, code: int = 0) -> None:
        """The stream's last line. A reader that sees it knows nothing follows, which is the one
        promise a tail cannot make for itself."""
        for sink in self.sinks:
            finish = getattr(sink, "finish_run", None)
            if finish is not None:
                finish(ok, code)
