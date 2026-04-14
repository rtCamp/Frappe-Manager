"""
Tests that --worker-kill-timeout and --worker-kill-poll flow correctly through
the entire call chain without being dropped or defaulted.

Chain under test:
  stop_service / restart_service
    → execute_supervisor_command
      → _handle_stop / _handle_restart
        → _stop_single_process_with_logic
          → _kill_process
            → _wait_for_process_stop
"""

import time
import unittest
from unittest.mock import MagicMock, patch, call

from fmx.supervisor.constants import ProcessStates
from fmx.supervisor.stop_helpers import _kill_process, _wait_for_process_stop, _stop_single_process_with_logic
from fmx.supervisor.actions import _handle_stop, _handle_restart
from fmx.supervisor.executor import execute_supervisor_command
from fmx.supervisor.api import stop_service, restart_service

RUNNING = ProcessStates.RUNNING
STOPPED = ProcessStates.STOPPED

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api(initial_state=RUNNING, stopped_after_signal=True):
    """Return a mock supervisor API where the process transitions to STOPPED
    after signalProcess is called (simulating a clean USR1 exit)."""
    api = MagicMock()
    call_count = [0]

    def get_info(name):
        call_count[0] += 1
        # First call (before signal): RUNNING. After that: STOPPED.
        state = RUNNING if call_count[0] == 1 else (STOPPED if stopped_after_signal else RUNNING)
        return {'state': state, 'name': name, 'group': None, 'pid': 1234}

    api.getProcessInfo.side_effect = get_info
    api.getAllProcessInfo.return_value = [{'state': RUNNING, 'name': 'long-worker', 'group': None, 'pid': 1234}]
    return api


# ---------------------------------------------------------------------------
# Level 1 — _wait_for_process_stop: poll_interval forwarded to time.sleep
# ---------------------------------------------------------------------------


class TestWaitForProcessStop(unittest.TestCase):
    def test_uses_custom_poll_interval(self):
        api = MagicMock()
        # Process stops on first check
        api.getProcessInfo.return_value = {'state': STOPPED}

        with patch('fmx.supervisor.stop_helpers.time.sleep') as mock_sleep:
            result = _wait_for_process_stop(api, 'long-worker', timeout=15, poll_interval=7.5)

        self.assertTrue(result)
        # sleep should NOT have been called because process was already stopped on first poll
        mock_sleep.assert_not_called()

    def test_poll_interval_used_between_checks(self):
        api = MagicMock()
        call_count = [0]

        def get_info(name):
            call_count[0] += 1
            return {'state': STOPPED if call_count[0] >= 2 else RUNNING}

        api.getProcessInfo.side_effect = get_info

        with patch('fmx.supervisor.stop_helpers.time.sleep') as mock_sleep:
            result = _wait_for_process_stop(api, 'long-worker', timeout=30, poll_interval=4.2)

        self.assertTrue(result)
        mock_sleep.assert_called_once_with(4.2)

    def test_default_poll_interval_is_3(self):
        """Verify default poll_interval is 3.0, not the old 0.5."""
        api = MagicMock()
        call_count = [0]

        def get_info(name):
            call_count[0] += 1
            return {'state': STOPPED if call_count[0] >= 2 else RUNNING}

        api.getProcessInfo.side_effect = get_info

        with patch('fmx.supervisor.stop_helpers.time.sleep') as mock_sleep:
            _wait_for_process_stop(api, 'long-worker', timeout=30)

        mock_sleep.assert_called_once_with(3.0)


# ---------------------------------------------------------------------------
# Level 2 — _kill_process: timeout + poll_interval reach _wait_for_process_stop
# ---------------------------------------------------------------------------


class TestKillProcess(unittest.TestCase):
    def test_custom_timeout_and_poll_forwarded(self):
        with patch('fmx.supervisor.stop_helpers._wait_for_process_stop', return_value=True) as mock_wait:
            api = MagicMock()
            api.getProcessInfo.return_value = {'state': RUNNING}

            _kill_process(api, 'svc', 'long-worker', timeout=42, poll_interval=6.0)

        mock_wait.assert_called_once_with(api, 'long-worker', timeout=42, poll_interval=6.0)

    def test_default_timeout_is_15_not_10(self):
        """Verify default timeout is 15 (not the old hardcoded 10)."""
        with patch('fmx.supervisor.stop_helpers._wait_for_process_stop', return_value=True) as mock_wait:
            api = MagicMock()
            api.getProcessInfo.return_value = {'state': RUNNING}

            _kill_process(api, 'svc', 'long-worker')

        _, kwargs = mock_wait.call_args
        self.assertEqual(kwargs['timeout'], 15)
        self.assertEqual(kwargs['poll_interval'], 3.0)

    def test_falls_back_to_stopprocess_on_timeout(self):
        """After timeout, stopProcess must be called as fallback."""
        api = MagicMock()
        api.getProcessInfo.return_value = {'state': RUNNING}

        with patch('fmx.supervisor.stop_helpers._wait_for_process_stop', return_value=False):
            _kill_process(api, 'svc', 'long-worker', timeout=5, poll_interval=1.0)

        api.stopProcess.assert_called_once_with('long-worker', True)


# ---------------------------------------------------------------------------
# Level 3 — _stop_single_process_with_logic: worker vs non-worker routing
# ---------------------------------------------------------------------------


class TestStopSingleProcess(unittest.TestCase):
    def test_worker_gets_kill_process_with_custom_params(self):
        with patch('fmx.supervisor.stop_helpers._kill_process', return_value=True) as mock_kill:
            api = MagicMock()
            _stop_single_process_with_logic(
                api,
                'svc',
                'long-worker',
                wait=True,
                wait_workers=False,
                process_info={'group': None},
                worker_kill_timeout=99,
                worker_kill_poll=8.0,
            )

        mock_kill.assert_called_once_with(api, 'svc', 'long-worker', timeout=99, poll_interval=8.0)

    def test_non_worker_uses_stopprocess_not_kill(self):
        """Non-worker processes must use stopProcess, kill params are irrelevant."""
        with patch('fmx.supervisor.stop_helpers._kill_process') as mock_kill:
            api = MagicMock()
            _stop_single_process_with_logic(
                api,
                'svc',
                'frappe',
                wait=True,
                wait_workers=False,
                process_info={'group': None},
                worker_kill_timeout=99,
                worker_kill_poll=8.0,
            )

        mock_kill.assert_not_called()
        api.stopProcess.assert_called_once()

    def test_worker_with_wait_workers_uses_stopprocess(self):
        """When wait_workers=True, workers use stopProcess (drain path), not kill."""
        with patch('fmx.supervisor.stop_helpers._kill_process') as mock_kill:
            api = MagicMock()
            _stop_single_process_with_logic(
                api,
                'svc',
                'long-worker',
                wait=True,
                wait_workers=True,
                process_info={'group': None},
                worker_kill_timeout=99,
                worker_kill_poll=8.0,
            )

        mock_kill.assert_not_called()
        api.stopProcess.assert_called_once()


# ---------------------------------------------------------------------------
# Level 4 — _handle_stop: params reach _stop_single_process_with_logic
# ---------------------------------------------------------------------------


class TestHandleStop(unittest.TestCase):
    def test_custom_params_forwarded_to_stop_single(self):
        api = _make_api()

        with patch('fmx.supervisor.actions._stop_single_process_with_logic', return_value=True) as mock_stop:
            _handle_stop(api, 'svc', None, wait=True, worker_kill_timeout=77, worker_kill_poll=5.5)

        self.assertTrue(mock_stop.called)
        _, kwargs = mock_stop.call_args
        self.assertEqual(kwargs['worker_kill_timeout'], 77)
        self.assertEqual(kwargs['worker_kill_poll'], 5.5)


# ---------------------------------------------------------------------------
# Level 5 — _handle_restart: params reach _handle_stop
# ---------------------------------------------------------------------------


class TestHandleRestart(unittest.TestCase):
    def test_custom_params_forwarded_through_restart(self):
        api = _make_api()

        with (
            patch(
                'fmx.supervisor.actions._handle_stop',
                return_value={'stopped': ['long-worker'], 'already_stopped': [], 'failed': []},
            ) as mock_stop,
            patch(
                'fmx.supervisor.actions._handle_start',
                return_value={'started': [], 'already_running': [], 'failed': []},
            ),
        ):
            _handle_restart(api, 'svc', None, wait=True, worker_kill_timeout=55, worker_kill_poll=2.5)

        _, kwargs = mock_stop.call_args
        self.assertEqual(kwargs['worker_kill_timeout'], 55)
        self.assertEqual(kwargs['worker_kill_poll'], 2.5)


# ---------------------------------------------------------------------------
# Level 6 — execute_supervisor_command: stop + restart actions
# ---------------------------------------------------------------------------


class TestExecuteSupervisorCommand(unittest.TestCase):
    def _patched_api(self):
        return patch(
            'fmx.supervisor.executor._get_validated_supervisor_api',
            return_value=_make_api(),
        )

    def test_stop_action_forwards_params(self):
        with self._patched_api(), patch('fmx.supervisor.executor._handle_stop', return_value={}) as mock_stop:
            execute_supervisor_command('svc', 'stop', worker_kill_timeout=33, worker_kill_poll=1.5)

        _, kwargs = mock_stop.call_args
        self.assertEqual(kwargs['worker_kill_timeout'], 33)
        self.assertEqual(kwargs['worker_kill_poll'], 1.5)

    def test_restart_action_forwards_params(self):
        with self._patched_api(), patch('fmx.supervisor.executor._handle_restart', return_value={}) as mock_restart:
            execute_supervisor_command('svc', 'restart', worker_kill_timeout=33, worker_kill_poll=1.5)

        _, kwargs = mock_restart.call_args
        self.assertEqual(kwargs['worker_kill_timeout'], 33)
        self.assertEqual(kwargs['worker_kill_poll'], 1.5)


# ---------------------------------------------------------------------------
# Level 7 — stop_service / restart_service: top of the Python API
# ---------------------------------------------------------------------------


class TestServiceAPI(unittest.TestCase):
    def test_stop_service_forwards_params(self):
        with patch('fmx.supervisor.api.execute_supervisor_command', return_value={}) as mock_exec:
            stop_service('svc', worker_kill_timeout=21, worker_kill_poll=9.0)

        _, kwargs = mock_exec.call_args
        self.assertEqual(kwargs['worker_kill_timeout'], 21)
        self.assertEqual(kwargs['worker_kill_poll'], 9.0)

    def test_restart_service_forwards_params(self):
        with patch('fmx.supervisor.api.execute_supervisor_command', return_value={}) as mock_exec:
            restart_service('svc', worker_kill_timeout=21, worker_kill_poll=9.0)

        _, kwargs = mock_exec.call_args
        self.assertEqual(kwargs['worker_kill_timeout'], 21)
        self.assertEqual(kwargs['worker_kill_poll'], 9.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
