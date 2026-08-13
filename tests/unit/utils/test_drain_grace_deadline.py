"""The post-exit drain gives up AT the grace deadline, not after it.

`stream_command_output` waits forever while the child is alive, but once the child has
exited a wedged reader thread only gets DRAIN_GRACE_PERIOD_AFTER_EXIT seconds to finish
posting. The comparison that ends the wait is `monotonic() < grace_deadline`, so the
instant the clock reaches the deadline the loop stops, warns, and returns the exit code.
That is the whole point of the bound: reaching the deadline must terminate the wait, not
buy one more poll interval. A line that only shows up at that instant is deliberately
lost -- trailing output is the thing we are willing to sacrifice to never hang fm.

The poll interval divides the grace period exactly, so "the clock lands exactly on the
deadline" is a state fm really reaches, not a contrived one; the first test asserts that
precondition rather than assuming it.
"""

from queue import Empty
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.utils import subprocess as subprocess_utils

EMPTY = object()

GRACE = subprocess_utils.DRAIN_GRACE_PERIOD_AFTER_EXIT
POLL = subprocess_utils.DRAIN_POLL_INTERVAL
# Idle polls counted from the first one (which observes the exit and arms the deadline).
# Poll 1 sets deadline = now + GRACE, so poll 1 + GRACE/POLL is the one that lands on it.
POLLS_TO_REACH_DEADLINE = int(GRACE / POLL) + 1


class _Clock:
    """Monotonic stand-in; the fake queue advances it by each get()'s timeout."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


class _Queue:
    def __init__(self, script, clock: _Clock):
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


class _Process:
    """Popen stand-in that has already exited."""

    def __init__(self):
        self.stdout = MagicMock(name="stdout")
        self.stderr = MagicMock(name="stderr")

    def poll(self):
        return 0

    def wait(self):
        return 0


def _drive(script):
    clock = _Clock()
    queue = _Queue(script, clock)
    logger = MagicMock(name="logger")

    with patch.multiple(
        subprocess_utils,
        Popen=MagicMock(return_value=_Process()),
        Queue=MagicMock(return_value=queue),
        Thread=MagicMock(name="Thread"),
        monotonic=clock,
        logger=logger,
    ):
        items = list(subprocess_utils.stream_command_output(["fake-cmd"]))

    return items, queue, logger


@pytest.mark.unit
class TestDrainStopsAtTheGraceDeadline:
    @pytest.mark.timeout(15)
    def test_output_arriving_exactly_at_the_deadline_is_not_waited_for(self):
        assert GRACE % POLL == 0, "the deadline is only reachable exactly if the poll interval divides it"

        script = [EMPTY] * POLLS_TO_REACH_DEADLINE + [("stdout", b"too-late"), None, None]
        items, queue, logger = _drive(script)

        # Reaching the deadline ends the wait: the queue is never read again, so the
        # line sitting in it is dropped and only the exit code comes out.
        assert items == [("exit_code", b"0")]
        assert queue.empty_waits == POLLS_TO_REACH_DEADLINE
        # ...and the caller is told trailing output went missing.
        assert logger.warning.call_count == 1
        assert "Trailing output may be missing" in logger.warning.call_args.args[0]

    @pytest.mark.timeout(15)
    def test_output_arriving_one_poll_before_the_deadline_is_still_delivered(self):
        """The other side of the same boundary: inside the window nothing is sacrificed."""
        script = [EMPTY] * (POLLS_TO_REACH_DEADLINE - 1) + [("stdout", b"just-in-time"), None, None]
        items, queue, logger = _drive(script)

        assert items == [("stdout", b"just-in-time"), ("exit_code", b"0")]
        assert queue.empty_waits == POLLS_TO_REACH_DEADLINE - 1
        logger.warning.assert_not_called()
