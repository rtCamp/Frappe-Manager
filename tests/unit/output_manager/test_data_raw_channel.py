"""The `data_raw` channel: a command's own result as exact bytes, on stdout, through the handler.

The channel exists because call sites reaching for `typer.echo` wrote straight past the stream
contract, the file log and the --json stream -- which is how `fm --json domain list` came to emit
lines that are not JSON into its own JSONL stream.
"""

import io
import json
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from frappe_manager.output_manager.json_output import JSONOutputHandler
from frappe_manager.output_manager.logging_output import LoggingOutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler


@pytest.mark.unit
class TestDataRawIsVerbatimOnStdout:
    def test_text_reaches_stdout_not_stderr(self):
        """Data is the caller's result, so it must survive `fm ... > file` and `| jq`."""
        output = RichOutputHandler()
        output.stdout = Console(file=io.StringIO())
        output.stderr = Console(file=io.StringIO())

        output.data_raw("mybench.localhost  primary")

        assert "mybench.localhost  primary" in output.stdout.file.getvalue()
        assert output.stderr.file.getvalue() == ""

    def test_markup_is_not_interpreted(self):
        """A relayed value containing `[INFO]` renders as nothing when rich markup is on."""
        output = RichOutputHandler()
        output.stdout = Console(file=io.StringIO())

        output.data_raw("[INFO] /srv/bench/site")

        assert "[INFO] /srv/bench/site" in output.stdout.file.getvalue()

    def test_a_long_path_is_not_broken_across_lines(self):
        """A wrapped path is a corrupted path once it is copied out of the terminal."""
        path = "/home/frappe/frappe/sites/" + "a" * 120 + "/workspace/frappe-bench"
        output = RichOutputHandler()
        output.stdout = Console(file=io.StringIO(), width=80)

        output.data_raw(path)

        assert path in output.stdout.file.getvalue().replace("\n", "")
        assert path in [line.strip() for line in output.stdout.file.getvalue().splitlines()]


@pytest.mark.unit
class TestDataRawReachesEveryDestination:
    def test_json_mode_emits_one_parseable_event(self):
        """In --json the JSONL stream owns stdout; a raw write there corrupts it."""
        stream = io.StringIO()
        output = JSONOutputHandler(stream=stream)

        output.data_raw("mybench.localhost  primary")

        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event_type"] == "data_raw"
        assert event["data"]["text"] == "mybench.localhost  primary"

    def test_the_logging_wrapper_forwards_and_records(self):
        """A method the wrapper forgets to forward is invisible through it (see emit_exit)."""
        delegate = MagicMock()
        output = LoggingOutputHandler(delegate)

        output.data_raw("mybench.localhost  primary")

        delegate.data_raw.assert_called_once_with("mybench.localhost  primary")
