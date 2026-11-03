"""Every logged line names the command that produced it, and which pipe it came from.

A run opens dozens of processes. Without the tag the file holds hundreds of output lines with
nothing saying which wrote any of them, which is the complaint this whole surface started from.
"""

import contextlib
import pathlib
import re
import tempfile

import pytest

from frappe_manager.logger import log as log_module
from frappe_manager.utils.docker import run_command_with_exit_code


@pytest.fixture
def logged():
    def _run(argv: list[str], **kwargs) -> str:
        directory = pathlib.Path(tempfile.mkdtemp())
        log_module.loggers.pop("fm", None)
        log_module.get_logger(log_dir=directory, log_file_name="fm", file_level="DEBUG")
        with contextlib.suppress(Exception):
            run_command_with_exit_code(argv, **kwargs)
        return (directory / "fm.log").read_text()

    return _run


@pytest.mark.unit
class TestALineNamesItsCommand:
    def test_output_carries_the_same_id_as_its_command_line(self, logged):
        text = logged(["/bin/sh", "-c", "echo hello"], stream=False)

        ids = set(re.findall(r"\[cmd=(\w{4})", text))
        assert len(ids) == 1
        assert "COMMAND:" in text
        assert "hello" in text

    def test_the_two_pipes_are_distinguishable(self, logged):
        text = logged(["/bin/sh", "-c", "echo out; echo err 1>&2"], stream=False)

        assert re.search(r"\[cmd=\w{4} out\] out", text)
        assert re.search(r"\[cmd=\w{4} err\] err", text)

    def test_two_commands_do_not_share_an_id(self, logged):
        first = set(re.findall(r"\[cmd=(\w{4})", logged(["/bin/sh", "-c", "echo a"], stream=False)))
        second = set(re.findall(r"\[cmd=(\w{4})", logged(["/bin/sh", "-c", "echo b"], stream=False)))

        assert first != second

    def test_the_exit_code_belongs_to_its_command_too(self, logged):
        text = logged(["/bin/sh", "-c", "exit 3"], stream=False)

        [(cmd_id,)] = {(i,) for i in re.findall(r"\[cmd=(\w{4})\]", text)} or [("",)]
        assert f"[cmd={cmd_id}] RETURN CODE: 3" in text

    def test_the_dashed_separators_are_gone(self, logged):
        """They marked where one command's lines began; the id says it per line now."""
        text = logged(["/bin/sh", "-c", "echo hello"], stream=False)

        assert "- -- --" not in text
