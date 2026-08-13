"""Unit tests for `frappe_manager.utils.subprocess.stream_command_output`.

Contract defended here:

* Every stdout/stderr line the reader threads queue is yielded, in arrival
  order, and the ("exit_code", ...) item is always yielded last with the child's
  real exit status.
* The drain loop is BOUNDED. A reader thread can wedge permanently (observed in
  production: blocked inside logging's flush while the rotating file handler
  gzips a rotated log under its lock), so its `finally: queue.put(None)` never
  runs. The old `iter(q.get, None)` drain had no timeout and froze fm forever.
  After the child has exited, the drain must give up on missing sentinels within
  DRAIN_GRACE_PERIOD_AFTER_EXIT, warn, and still yield the exit code.
* The bound must NOT be a wall-clock cap on the command: while the child is
  still running (`process.poll() is None`) an arbitrarily quiet command keeps
  waiting. `fm create` is silent for minutes and must not be cut short.

The bounded-drain tests drive the generator with a fake Popen/Queue/Thread trio
plus a fake monotonic clock, so they exercise the real production constants
without spending real time and without launching a real subprocess.
"""

from queue import Empty
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.utils import subprocess as subprocess_utils

# Script marker: this q.get() call should raise Empty (and burn its timeout).
EMPTY = object()


class FakeClock:
    """Monotonic clock stand-in advanced explicitly by the fake queue."""

    def __init__(self, start: float = 1000.0):
        self.start = start
        self.now = start

    def __call__(self) -> float:
        return self.now

    @property
    def elapsed(self) -> float:
        return self.now - self.start


class FakeQueue:
    """Queue stand-in replaying a scripted sequence of gets."""

    def __init__(self, script, clock: FakeClock):
        self._script = list(script)
        self._clock = clock
        self.empty_waits = 0

    def get(self, timeout=None):
        if not self._script:
            raise AssertionError("drain loop consumed the whole script and is still asking for more")
        item = self._script.pop(0)
        if item is EMPTY:
            self.empty_waits += 1
            self._clock.now += timeout
            raise Empty
        return item


class FakeProcess:
    """Popen stand-in: alive for the first `alive_polls` poll() calls."""

    def __init__(self, returncode: int = 0, alive_polls: int = 0):
        self.stdout = MagicMock(name="stdout")
        self.stderr = MagicMock(name="stderr")
        self.returncode = returncode
        self.alive_polls = alive_polls
        self.polls = 0
        self.waited = False

    def poll(self):
        self.polls += 1
        if self.polls <= self.alive_polls:
            return None
        return self.returncode

    def wait(self):
        self.waited = True
        return self.returncode


def drive(script, *, returncode: int = 0, alive_polls: int = 0):
    """Run stream_command_output against the scripted fake queue.

    Returns (yielded_items, process, fake_queue, clock, fake_logger).
    """
    clock = FakeClock()
    process = FakeProcess(returncode=returncode, alive_polls=alive_polls)
    queue = FakeQueue(script, clock)
    fake_logger = MagicMock(name="logger")

    with patch.multiple(
        subprocess_utils,
        Popen=MagicMock(return_value=process),
        Queue=MagicMock(return_value=queue),
        Thread=MagicMock(name="Thread"),
        monotonic=clock,
        logger=fake_logger,
    ):
        items = list(subprocess_utils.stream_command_output(["fake-cmd"]))

    return items, process, queue, clock, fake_logger


@pytest.mark.unit
class TestStreamCommandOutputHappyPath:
    def test_yields_stdout_and_stderr_then_exit_code(self):
        items = list(subprocess_utils.stream_command_output(["/bin/sh", "-c", "echo out; echo err 1>&2"]))

        assert items[-1] == ("exit_code", b"0")
        assert ("stdout", b"out") in items
        assert ("stderr", b"err") in items
        assert len(items) == 3

    def test_command_with_no_output_yields_only_exit_code(self):
        items = list(subprocess_utils.stream_command_output(["/bin/sh", "-c", ":"]))

        assert items == [("exit_code", b"0")]

    def test_non_zero_exit_code_is_reported(self):
        items = list(subprocess_utils.stream_command_output(["/bin/sh", "-c", "echo bye; exit 7"]))

        assert items == [("stdout", b"bye"), ("exit_code", b"7")]

    def test_multiple_lines_are_yielded_in_arrival_order(self):
        # Fake queue => deterministic ordering, unlike two real reader threads.
        script = [
            ("stdout", b"one"),
            ("stderr", b"two"),
            ("stdout", b"three"),
            None,
            None,
        ]
        items, process, _queue, _clock, _logger = drive(script, returncode=0)

        assert items == [
            ("stdout", b"one"),
            ("stderr", b"two"),
            ("stdout", b"three"),
            ("exit_code", b"0"),
        ]
        assert process.waited

    def test_sentinels_are_not_yielded_as_output(self):
        items, _process, _queue, _clock, _logger = drive([None, ("stdout", b"tail"), None])

        assert items == [("stdout", b"tail"), ("exit_code", b"0")]


@pytest.mark.unit
class TestStreamCommandOutputWedgedReader:
    @pytest.mark.timeout(30)
    def test_wedged_reader_after_exit_still_yields_exit_code(self):
        """REGRESSION: one reader never posts its sentinel and the child exited.

        The old unbounded `iter(q.get, None)` drain blocked here forever.
        """
        # One sentinel arrives, the second never does; then the queue is silent
        # for far longer than the grace period.
        script = [("stdout", b"partial"), None] + [EMPTY] * 60
        items, process, queue, clock, fake_logger = drive(script, returncode=5)

        assert items == [("stdout", b"partial"), ("exit_code", b"5")]
        assert process.waited
        # Gave up only after the full grace period, not on the first Empty.
        assert clock.elapsed >= subprocess_utils.DRAIN_GRACE_PERIOD_AFTER_EXIT
        assert queue.empty_waits >= 2
        # And said why, naming the wedged reader thread.
        assert fake_logger.warning.call_count == 1
        message = fake_logger.warning.call_args.args[0]
        assert "wedged" in message

    @pytest.mark.timeout(30)
    def test_both_readers_wedged_still_yields_exit_code(self):
        items, process, _queue, _clock, fake_logger = drive([EMPTY] * 60, returncode=0)

        assert items == [("exit_code", b"0")]
        assert process.waited
        assert fake_logger.warning.call_count == 1


@pytest.mark.unit
class TestStreamCommandOutputQuietRunningChild:
    @pytest.mark.timeout(30)
    def test_quiet_but_running_child_is_not_cut_short(self):
        """A long-running silent command must never be abandoned.

        The child stays alive (poll() -> None) through 30 idle polls, i.e. well
        past DRAIN_GRACE_PERIOD_AFTER_EXIT of fake time, and only then produces
        its output. All of it must still be delivered.
        """
        idle_polls = 30
        script = [EMPTY] * idle_polls + [("stdout", b"late"), None, None]
        items, process, queue, clock, fake_logger = drive(script, returncode=0, alive_polls=idle_polls)

        assert items == [("stdout", b"late"), ("exit_code", b"0")]
        assert queue.empty_waits == idle_polls
        # Waited far longer than the post-exit grace period, because the child
        # was still running the whole time.
        assert clock.elapsed > subprocess_utils.DRAIN_GRACE_PERIOD_AFTER_EXIT
        assert process.waited
        fake_logger.warning.assert_not_called()

    @pytest.mark.timeout(30)
    def test_grace_period_only_starts_once_the_child_has_exited(self):
        # Alive for 5 idle polls, then exited; the sentinels arrive during the
        # grace window that follows, so nothing is dropped and nothing is warned.
        script = [EMPTY] * 5 + [EMPTY, ("stderr", b"flush"), None, None]
        items, _process, _queue, _clock, fake_logger = drive(script, returncode=1, alive_polls=5)

        assert items == [("stderr", b"flush"), ("exit_code", b"1")]
        fake_logger.warning.assert_not_called()
