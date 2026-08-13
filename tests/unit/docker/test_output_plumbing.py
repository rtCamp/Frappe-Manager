"""The output plumbing every docker call funnels through.

`SubprocessOutput.from_output` and the two wrappers in `utils/docker.py` sit between raw process
output and every caller in fm: they decide how a line is classified, what `combined` contains, what
the exit code is, and whether a non-zero exit becomes a `DockerException` carrying the output.

These lines used to be executed only as a side effect of a test that made a REAL `docker compose`
call and asserted nothing about them (it also hung intermittently). That test is now hermetic, so
this file pins the same code deliberately, with assertions.

No docker daemon, no network: the wrappers take an argv list, so trivial `/bin/sh` commands
exercise the real streaming path instantly.
"""

import pytest

from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.utils.docker import run_command_with_exit_code, stream_stdout_and_stderr


def _pairs(*items: tuple[str, str]) -> list[tuple[str, bytes]]:
    """Build the (source, bytes) stream shape that from_output consumes."""
    return [(source, text.encode()) for source, text in items]


class TestSubprocessOutputFromOutput:
    def test_classifies_each_source_and_decodes(self):
        out = SubprocessOutput.from_output(
            _pairs(("stdout", "first"), ("stderr", "problem"), ("stdout", "second"), ("exit_code", "0")),
        )

        assert out.stdout == ["first", "second"]
        assert out.stderr == ["problem"]
        assert out.exit_code == 0

    def test_combined_holds_output_in_arrival_order_and_excludes_the_exit_code(self):
        """`combined` is what callers grep for messages, so interleaving and exclusion both matter."""
        out = SubprocessOutput.from_output(
            _pairs(("stdout", "a"), ("stderr", "b"), ("stdout", "c"), ("exit_code", "1")),
        )

        assert out.combined == ["a", "b", "c"]
        assert "1" not in out.combined

    def test_exit_code_is_parsed_as_an_int(self):
        out = SubprocessOutput.from_output(_pairs(("exit_code", "7")))

        assert out.exit_code == 7
        assert isinstance(out.exit_code, int)

    def test_exit_code_defaults_to_zero_when_the_stream_never_reports_one(self):
        out = SubprocessOutput.from_output(_pairs(("stdout", "orphan line")))

        assert out.exit_code == 0

    def test_empty_stream_produces_empty_lists_not_none(self):
        out = SubprocessOutput.from_output([])

        assert (out.stdout, out.stderr, out.combined, out.exit_code) == ([], [], [], 0)

    def test_an_unknown_source_still_lands_in_combined(self):
        """Anything that is not the exit code counts as output, whatever the source label is."""
        out = SubprocessOutput.from_output(_pairs(("other", "surprise")))

        assert out.combined == ["surprise"]
        assert out.stdout == []
        assert out.stderr == []


class TestStreamStdoutAndStderr:
    def test_yields_output_then_the_exit_code(self):
        items = list(stream_stdout_and_stderr(["/bin/sh", "-c", "echo hello"]))

        assert ("stdout", b"hello") in items
        assert items[-1][0] == "exit_code"
        assert items[-1][1] == b"0"

    def test_separates_stderr_from_stdout(self):
        items = list(stream_stdout_and_stderr(["/bin/sh", "-c", "echo out; echo err 1>&2"]))
        by_source = {source: line for source, line in items if source != "exit_code"}

        assert by_source["stdout"] == b"out"
        assert by_source["stderr"] == b"err"

    def test_non_zero_exit_raises_docker_exception_carrying_the_output(self):
        """The exception is fm's only channel for a failed docker command, so it must carry detail."""
        with pytest.raises(DockerException) as exc_info:
            list(stream_stdout_and_stderr(["/bin/sh", "-c", "echo boom 1>&2; exit 3"]))

        assert exc_info.value.output.exit_code == 3
        assert "boom" in exc_info.value.output.combined

    def test_is_lazy_so_a_long_command_streams_rather_than_buffers(self):
        """Callers rely on this being a generator to render live output."""
        stream = iter(stream_stdout_and_stderr(["/bin/sh", "-c", "echo one; echo two"]))

        assert next(stream) == ("stdout", b"one")


class TestRunCommandWithExitCode:
    def test_streaming_mode_returns_an_iterator(self):
        result = run_command_with_exit_code(["/bin/sh", "-c", "echo streamed"], stream=True)

        assert not isinstance(result, SubprocessOutput), "stream=True must hand back the raw iterator"
        assert ("stdout", b"streamed") in list(result)

    def test_capture_mode_returns_a_populated_subprocess_output(self):
        result = run_command_with_exit_code(["/bin/sh", "-c", "echo captured"], stream=False, capture_output=True)

        assert isinstance(result, SubprocessOutput)
        assert result.combined == ["captured"]
        assert result.exit_code == 0

    def test_capture_mode_raises_on_failure(self):
        with pytest.raises(DockerException):
            run_command_with_exit_code(["/bin/sh", "-c", "exit 4"], stream=False, capture_output=True)

    def test_fire_and_forget_mode_returns_none_on_success(self):
        result = run_command_with_exit_code(["/bin/sh", "-c", "exit 0"], stream=False, capture_output=False)

        assert result is None

    def test_fire_and_forget_mode_raises_with_the_exit_code_on_failure(self):
        with pytest.raises(DockerException) as exc_info:
            run_command_with_exit_code(["/bin/sh", "-c", "exit 5"], stream=False, capture_output=False)

        assert exc_info.value.output.exit_code == 5

    def test_input_data_is_fed_to_the_commands_stdin(self):
        """This is the `docker login --password-stdin` path: a secret must reach stdin, not argv."""
        result = run_command_with_exit_code(
            ["/bin/sh", "-c", 'read value; test "$value" = "s3cret"'],
            stream=False,
            capture_output=False,
            input_data=b"s3cret\n",
        )

        assert result is None
