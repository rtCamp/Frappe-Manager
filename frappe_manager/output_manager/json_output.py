"""
JSON event output handler.

This implementation captures all output events as structured JSON data,
suitable for API responses, logging, or testing. No actual output is
displayed to the terminal.
"""

import json
from collections.abc import Iterator
from typing import Any

from frappe_manager.output_manager.base import OutputHandler


class OutputEvent:
    """Represents a single output event."""

    def __init__(self, event_type: str, data: dict):
        """
        Initialize an output event.

        Args:
            event_type: Type of event (e.g., "start", "print", "error")
            data: Event-specific data
        """
        self.event_type = event_type
        self.data = data

    def to_dict(self) -> dict:
        """
        Convert event to dictionary.

        Returns:
            Dictionary representation of the event
        """
        return {"event_type": self.event_type, "data": self.data}

    def to_json(self) -> str:
        """
        Convert event to JSON string.

        Returns:
            JSON string representation of the event
        """
        return json.dumps(self.to_dict())


class JSONOutputHandler(OutputHandler):
    """
    Output handler that captures events as structured JSON data.

    This handler is suitable for:
    - API responses (FastAPI, Flask, etc.)
    - Structured logging
    - Testing and assertions
    - WebSocket communication

    All events are captured in the `events` list and can be retrieved
    as dictionaries or JSON strings.
    """

    def __init__(self):
        """Initialize the JSON output handler."""
        self.events: list[OutputEvent] = []
        self._current_head: str | None = None
        self._is_started: bool = False

    def start(self, text: str) -> None:
        """
        Start a new operation with a status message.

        Args:
            text: The initial status message
        """
        self._current_head = text
        self._is_started = True
        self.events.append(OutputEvent("start", {"text": text}))

    def change_head(self, text: str, style: str | None = None) -> None:
        """
        Update the current operation status message.

        Args:
            text: The new status message
            style: Optional style hint (ignored in JSON output)
        """
        previous_head = self._current_head
        self._current_head = text
        self.events.append(
            OutputEvent("change_head", {"text": text, "previous": previous_head, "style": style}),
        )

    def update_head(self, text: str) -> None:
        """
        Update the head text.

        Args:
            text: The new head text
        """
        previous_head = self._current_head
        self._current_head = text
        self.events.append(OutputEvent("update_head", {"text": text, "previous": previous_head}))

    def stop(self) -> None:
        """
        Stop the current operation status display.
        """
        self._is_started = False
        self.events.append(OutputEvent("stop", {}))

    def print(self, text: str, emoji_code: str = ":zap:", prefix: str | None = None, **kwargs) -> None:
        """
        Print a message.

        Args:
            text: The message to print
            emoji_code: Emoji code (captured but not rendered)
            prefix: Optional prefix for the message
            **kwargs: Additional arguments (captured in data)
        """
        self.events.append(
            OutputEvent(
                "print",
                {"text": text, "emoji_code": emoji_code, "prefix": prefix, "kwargs": kwargs},
            ),
        )

    def error(self, text: str, exception: Exception | None = None, emoji_code: str = ":no_entry:") -> None:
        """
        Display an error message.

        Args:
            text: The error message
            exception: Optional exception to raise after capturing
            emoji_code: Emoji code (captured but not rendered)
        """
        self.events.append(
            OutputEvent(
                "error",
                {
                    "text": text,
                    "emoji_code": emoji_code,
                    "exception": str(exception) if exception else None,
                    "exception_type": type(exception).__name__ if exception else None,
                },
            ),
        )
        if exception:
            raise exception

    def warning(self, text: str, emoji_code: str = ":warning:") -> None:
        """
        Display a warning message.

        Args:
            text: The warning message
            emoji_code: Emoji code (captured but not rendered)
        """
        self.events.append(OutputEvent("warning", {"text": text, "emoji_code": emoji_code}))

    def live_lines(
        self,
        data: Iterator[tuple[str, bytes]],
        stdout: bool = True,
        stderr: bool = True,
        lines: int = 4,
        padding: tuple[int, int, int, int] = (0, 0, 0, 2),
        stop_string: str | None = None,
        log_prefix: str = "=>",
    ) -> None:
        """
        Display live streaming output from a process.

        Args:
            data: Iterator yielding (source, line) tuples
            stdout: Whether to capture stdout lines
            stderr: Whether to capture stderr lines
            lines: Maximum number of lines (hint only)
            padding: Padding (ignored in JSON output)
            stop_string: String that stops capture when found
            log_prefix: Prefix for each line
        """
        captured_lines: list[dict] = []

        for source, line in data:
            try:
                decoded_line = line.decode()
            except Exception:
                decoded_line = str(line)

            if source == "stdout" and not stdout:
                continue
            if source == "stderr" and not stderr:
                continue

            captured_lines.append({"source": source, "line": decoded_line})

            if stop_string and stop_string.lower() in decoded_line.lower():
                break

        self.events.append(
            OutputEvent(
                "live_lines",
                {
                    "lines": captured_lines,
                    "stdout": stdout,
                    "stderr": stderr,
                    "max_lines": lines,
                    "stop_string": stop_string,
                    "log_prefix": log_prefix,
                },
            ),
        )

    def update_live(self, renderable: Any = None, padding: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        """
        Update the live display with new content.

        Args:
            renderable: Content to display (captured as string)
            padding: Padding (ignored in JSON output)
        """
        self.events.append(
            OutputEvent("update_live", {"renderable": str(renderable) if renderable else None, "padding": padding}),
        )

    def prompt_ask(self, **kwargs) -> str:
        """
        Prompt the user for input.

        Note: In JSON mode, this returns an empty string and logs the prompt.
        For actual user interaction, use RichOutputHandler.

        Args:
            **kwargs: Prompt arguments

        Returns:
            Empty string (no actual user input in JSON mode)
        """
        self.events.append(OutputEvent("prompt_ask", {"kwargs": kwargs}))
        return ""

    def get_events(self) -> list[dict]:
        """
        Get all captured events as dictionaries.

        Returns:
            List of event dictionaries
        """
        return [event.to_dict() for event in self.events]

    def get_events_json(self) -> str:
        """
        Get all captured events as JSON string.

        Returns:
            JSON string containing all events
        """
        return json.dumps(self.get_events(), indent=2)

    def clear_events(self) -> None:
        """
        Clear all captured events.
        """
        self.events.clear()
        self._current_head = None
        self._is_started = False
