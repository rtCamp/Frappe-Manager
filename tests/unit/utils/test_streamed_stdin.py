"""The streaming helper feeds standard input, which is what let the site creation stop
inheriting the terminal.

It opened output pipes only before this, so the one call that must write to a child's stdin had
to use the path that hands the terminal over -- and whose output therefore reached nothing.
"""

import pytest

from frappe_manager.utils.subprocess import stream_command_output


def _collect(argv: list[str], **kwargs) -> tuple[list[str], int | None]:
    lines: list[str] = []
    code: int | None = None
    for source, line in stream_command_output(argv, **kwargs):
        if source == "exit_code":
            code = int(line.decode())
        else:
            lines.append(line.decode())
    return lines, code


@pytest.mark.unit
class TestStandardInputReachesTheChild:
    def test_the_child_reads_what_it_was_given(self):
        lines, code = _collect(["/bin/cat"], input_data=b"hello from stdin\n")

        assert lines == ["hello from stdin"]
        assert code == 0

    def test_the_pipe_is_closed_so_a_reader_finishes(self):
        """Without the close a child reading until EOF never exits, and the run hangs forever."""
        lines, code = _collect(["/bin/sh", "-c", "wc -l"], input_data=b"a\nb\nc\n")

        assert code == 0
        assert lines[0].strip() == "3"

    def test_a_command_given_nothing_still_runs(self):
        """No stdin pipe is opened at all in that case; opening one would change what the child
        inherits for every command fm runs."""
        lines, code = _collect(["/bin/sh", "-c", "echo fine"])

        assert lines == ["fine"]
        assert code == 0

    def test_output_is_still_streamed_while_stdin_is_fed(self):
        lines, code = _collect(["/bin/sh", "-c", "cat; echo done 1>&2"], input_data=b"x\n")

        assert "x" in lines
        assert "done" in lines
        assert code == 0
