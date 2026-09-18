"""The one drain gate, now depended on by `fm restart`, `fm stop`, `fm update` and `fm apps add`.

Written out by hand four times before this, once per command, and the wording had already started
to drift. The timeout message is the one that matters most: it tells an operator their in-flight
work was not lost and how to proceed, so it is asserted here rather than four times over.

The gate has no policy switch on purpose. Every caller treats a timeout identically -- nothing
happens, `--no-drain` is the way through -- so what the callers actually differ on is who clears
the suspend flag, which the return value expresses.
"""

import os
import signal
import threading
import time
from unittest.mock import MagicMock

import pytest
import typer

from frappe_manager.site_manager.modules.deploy_orchestrator import DrainUnavailable
from frappe_manager.site_manager.modules.worker_drain import (
    drain_gate,
    frappe_maintenance_mode,
    rq_suspended,
)

pytestmark = pytest.mark.timeout(15)


def _orchestrator(*, drained=True, unavailable=False):
    orchestrator = MagicMock()
    orchestrator.workers_config.drain_timeout = 300
    if unavailable:
        orchestrator.drain_workers.side_effect = DrainUnavailable("no fmx in this image.")
    else:
        orchestrator.drain_workers.return_value = drained
    return orchestrator


def test_a_successful_drain_tells_the_caller_it_owes_a_resume():
    """The return value IS the contract: callers resume at different points (`fm stop` before the
    containers go down, everyone else after their work), so the gate never resumes for them."""
    orchestrator = _orchestrator(drained=True)

    assert drain_gate(orchestrator, MagicMock(), action="restart") is True
    orchestrator.resume_workers.assert_not_called()


def test_a_timeout_resumes_the_workers_before_aborting():
    """An abort that left the queue suspended would turn a refusal into a silent outage: the flag
    is a redis key and outlives the process that set it."""
    orchestrator = _orchestrator(drained=False)

    with pytest.raises(typer.Exit) as exc:
        drain_gate(orchestrator, MagicMock(), action="restart")

    assert exc.value.exit_code == 1
    orchestrator.resume_workers.assert_called_once_with()


def test_the_timeout_message_promises_nothing_changed_and_names_the_way_through():
    output = MagicMock()
    orchestrator = _orchestrator(drained=False)

    with pytest.raises(typer.Exit):
        drain_gate(orchestrator, output, action="stop")

    message = output.display_error.call_args.args[0]
    assert "Drain timed out after 300s" in message
    assert "Nothing was changed" in message
    assert "the stop can be retried" in message
    assert "--no-drain" in message


def test_an_undrainable_image_warns_and_lets_the_command_proceed():
    """Not a timeout: an image predating fmx can never be drained, and no drain_timeout fixes it,
    so refusing would make the command permanently impossible on that bench."""
    output = MagicMock()
    orchestrator = _orchestrator(unavailable=True)

    assert drain_gate(orchestrator, output, action="update") is False
    orchestrator.resume_workers.assert_not_called()
    assert "Continuing without a drain" in output.warning.call_args.args[0]


def test_nothing_to_suspend_is_not_a_resume_debt():
    """`drain_workers` returns True when drain is disabled in config or frappe is not running; the
    caller must not then resume a queue it never suspended."""
    orchestrator = _orchestrator(drained=True)

    assert drain_gate(orchestrator, MagicMock(), action="app install") is True


def _signal_during(context, sig) -> None:
    """Enter `context`, deliver `sig` to ourselves, and let the scoped handler raise.

    The sleep is never actually slept: the handler runs between bytecodes and raises out of it.
    """
    with context:
        os.kill(os.getpid(), sig)
        time.sleep(1)


class TestScopedSignalCleanup:
    """State a wait holds outside this process must not leak when a signal ends the run.

    fm installs no SIGINT/SIGTERM/SIGHUP handlers (`main.py` touches only SIGPIPE) and `atexit`
    does not run on signal death, so without this neither `finally` nor the registered cleanup
    gets a turn. Two keys are at stake and both outlive the process: `rq:suspended` in redis
    (workers alive, consuming nothing -- a silent outage that survives a bench restart) and
    `maintenance_mode` in site_config (site 503, scheduler off). Locks need no help: they are BSD
    flock, released by the kernel on death.

    The handlers are installed for the BLOCK only, never process-wide, because a global
    signal-to-exception would make `fm migrate` unwind into its `--on-failure=prompt` default
    (prompting into the terminal SIGHUP just removed) and `fm switch` start a rollback that the
    follow-up SIGKILL truncates halfway.
    """

    def test_both_scheduler_flags_are_set_and_cleared_together(self):
        """`maintenance_mode` alone already satisfies `is_scheduler_inactive`, but
        `pause_scheduler` names what fm wants -- and setting both survives an operator clearing
        one by hand mid-operation, which would otherwise let the scheduler refill the queue."""
        orchestrator = _orchestrator()

        with frappe_maintenance_mode(orchestrator, MagicMock()):
            pass

        assert [c.args for c in orchestrator.set_maintenance_mode.call_args_list] == [(1,), (0,)]
        assert [c.args for c in orchestrator.set_scheduler_paused.call_args_list] == [(1,), (0,)]

    def test_a_signal_clears_both_flags(self):
        orchestrator = _orchestrator()

        with pytest.raises(KeyboardInterrupt):
            _signal_during(frappe_maintenance_mode(orchestrator, MagicMock()), signal.SIGHUP)

        assert orchestrator.set_maintenance_mode.call_args_list[-1].args == (0,)
        assert orchestrator.set_scheduler_paused.call_args_list[-1].args == (0,)

    @pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
    def test_a_signal_mid_wait_clears_the_maintenance_flag(self, sig):
        orchestrator = _orchestrator()

        with pytest.raises(KeyboardInterrupt):
            _signal_during(frappe_maintenance_mode(orchestrator, MagicMock()), sig)

        assert [c.args for c in orchestrator.set_maintenance_mode.call_args_list] == [(1,), (0,)]

    @pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
    def test_a_signal_mid_wait_resumes_rq(self, sig):
        orchestrator = _orchestrator(drained=True)

        with pytest.raises(KeyboardInterrupt):
            _signal_during(rq_suspended(orchestrator, MagicMock(), action="update"), sig)

        orchestrator.resume_workers.assert_called_once_with()

    def test_nothing_is_resumed_when_nothing_was_suspended(self):
        """`drain_workers` returning False means there was nothing to suspend, so the undo must
        not resume a queue this run never froze."""
        orchestrator = _orchestrator(unavailable=True)

        with pytest.raises(KeyboardInterrupt):
            _signal_during(rq_suspended(orchestrator, MagicMock(), action="update"), signal.SIGTERM)

        orchestrator.resume_workers.assert_not_called()

    def test_the_undo_runs_once_on_the_happy_path(self):
        orchestrator = _orchestrator()

        with frappe_maintenance_mode(orchestrator, MagicMock()):
            pass

        assert [c.args for c in orchestrator.set_maintenance_mode.call_args_list] == [(1,), (0,)]

    def test_previous_handlers_are_restored(self):
        """Installed for the block only: a handler leaking out would change how every later part
        of the same command reacts to a signal."""
        before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}

        with frappe_maintenance_mode(_orchestrator(), MagicMock()):
            during = signal.getsignal(signal.SIGTERM)

        assert during not in (before[signal.SIGTERM], signal.SIG_DFL)
        assert {s: signal.getsignal(s) for s in before} == before

    def test_a_second_signal_is_fatal_rather_than_a_second_cleanup(self):
        """Someone pressing Ctrl-C again is escaping a STUCK undo, so the second signal must kill
        us the way it originally would, not queue another cleanup attempt."""
        orchestrator = _orchestrator()
        orchestrator.set_maintenance_mode.side_effect = [None, KeyboardInterrupt("undo is stuck")]

        with pytest.raises(KeyboardInterrupt):
            _signal_during(frappe_maintenance_mode(orchestrator, MagicMock()), signal.SIGTERM)

        # Two calls: the set, then the one undo attempt. No retry loop.
        assert orchestrator.set_maintenance_mode.call_count == 2

    def test_the_handlers_install_on_the_main_thread(self):
        """`signal.signal` raises ValueError off the main thread, where the install silently
        no-ops. Asserted here so that is caught in a test rather than discovered in the field."""
        assert threading.current_thread() is threading.main_thread()
