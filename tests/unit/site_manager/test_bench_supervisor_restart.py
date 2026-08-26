"""Tests for BenchSupervisor.restart_supervisor_service graceful-reload behaviour.

Covers the three branches of the restart path:

* ``force=True`` - hard restart (stop + start).
* ``graceful=True`` - SIGHUP via supervisorctl (no listening-socket drop).
* default - supervisorctl restart all.

Also covers the mutual exclusion of ``force`` and ``graceful`` and the wiring
from ``Bench.restart_web_containers_services`` (graceful is only honoured for
the frappe/gunicorn container, never for socketio).
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.exceptions import BenchOperationException
from frappe_manager.site_manager.modules.bench_supervisor import BenchSupervisor
from frappe_manager.site_manager.site import Bench, SiteServicesEnum


def _make_supervisor(docker_client: MagicMock) -> BenchSupervisor:
    """Build a BenchSupervisor with the heavy collaborators stubbed out."""
    supervisor = BenchSupervisor.__new__(BenchSupervisor)
    supervisor.bench_name = "test.localhost"
    supervisor.docker_client = docker_client
    supervisor.config = MagicMock()
    supervisor.output = MagicMock()
    supervisor.logger = MagicMock()
    return supervisor


def _stub_running_service(docker_client: MagicMock, service: str = "frappe") -> None:
    """Wire ``compose.get_all_services_status`` to report ``service`` as running."""
    docker_client.compose.get_all_services_status.return_value = [
        {"Service": service, "State": "running"},
    ]


class TestRestartSupervisorServiceGraceful:
    """The ``graceful`` branch must send ``supervisorctl signal HUP all`` exactly once."""

    def test_graceful_sends_sighup_via_supervisorctl(self):
        docker_client = MagicMock()
        _stub_running_service(docker_client)
        supervisor = _make_supervisor(docker_client)

        result = supervisor.restart_supervisor_service(
            service="frappe", graceful=True, timeout=1, interval=0,
        )

        assert result is True

        exec_calls = docker_client.compose.exec.call_args_list
        # First call is the SIGHUP; later calls are the socket-existence probe.
        first_kwargs = exec_calls[0].kwargs
        assert first_kwargs["service"] == "frappe"
        assert first_kwargs["user"] == "frappe"
        assert "signal HUP all" in first_kwargs["command"]
        assert "supervisorctl" in first_kwargs["command"]

        # Regression guard: SIGHUP must be issued exactly once. Any subsequent exec
        # calls are the socket-existence probe (``test -e ...``), never another HUP.
        sighup_calls = [
            call for call in exec_calls if "signal HUP all" in call.kwargs.get("command", "")
        ]
        assert len(sighup_calls) == 1

    def test_graceful_does_not_stop_then_start(self):
        """Graceful must never issue a stop or start - only signal HUP."""
        docker_client = MagicMock()
        _stub_running_service(docker_client)
        supervisor = _make_supervisor(docker_client)

        supervisor.restart_supervisor_service(
            service="frappe", graceful=True, timeout=1, interval=0,
        )

        issued_commands = [
            call.kwargs.get("command", "") for call in docker_client.compose.exec.call_args_list
        ]
        assert not any("stop all" in cmd for cmd in issued_commands)
        assert not any("start all" in cmd for cmd in issued_commands)
        assert not any("restart all" in cmd for cmd in issued_commands)

    def test_graceful_and_force_are_mutually_exclusive(self):
        docker_client = MagicMock()
        _stub_running_service(docker_client)
        supervisor = _make_supervisor(docker_client)

        with pytest.raises(BenchOperationException):
            supervisor.restart_supervisor_service(
                service="frappe", graceful=True, force=True, timeout=1, interval=0,
            )

        docker_client.compose.exec.assert_not_called()

    def test_graceful_propagates_docker_failure_as_bench_exception(self):
        docker_client = MagicMock()
        _stub_running_service(docker_client)
        supervisor = _make_supervisor(docker_client)

        docker_client.compose.exec.side_effect = DockerException(
            ["supervisorctl"],
            SubprocessOutput(stdout=[], stderr=["boom"], combined=["boom"], exit_code=1),
        )

        with pytest.raises(BenchOperationException) as exc_info:
            supervisor.restart_supervisor_service(
                service="frappe", graceful=True, timeout=1, interval=0,
            )

        assert "signal HUP" in str(exc_info.value)


class TestRestartSupervisorServiceForceVsDefault:
    """Regression checks for the pre-existing force and default branches."""

    def test_force_uses_stop_then_start(self):
        docker_client = MagicMock()
        _stub_running_service(docker_client)
        supervisor = _make_supervisor(docker_client)

        supervisor.restart_supervisor_service(
            service="frappe", force=True, timeout=1, interval=0,
        )

        issued_commands = [
            call.kwargs.get("command", "") for call in docker_client.compose.exec.call_args_list
        ]
        assert any("stop all" in cmd for cmd in issued_commands)
        assert any("start all" in cmd for cmd in issued_commands)
        assert not any("signal HUP" in cmd for cmd in issued_commands)

    def test_default_uses_restart_all(self):
        docker_client = MagicMock()
        _stub_running_service(docker_client)
        supervisor = _make_supervisor(docker_client)

        supervisor.restart_supervisor_service(
            service="frappe", timeout=1, interval=0,
        )

        issued_commands = [
            call.kwargs.get("command", "") for call in docker_client.compose.exec.call_args_list
        ]
        assert any("restart all" in cmd for cmd in issued_commands)
        assert not any("signal HUP" in cmd for cmd in issued_commands)


class TestRestartWebContainersServicesGracefulWiring:
    """``Bench.restart_web_containers_services`` must scope ``graceful`` to frappe only."""

    def _make_bench(self) -> Bench:
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.output = MagicMock()
        bench.docker_ops = MagicMock()
        bench.restart_supervisor_service = MagicMock(return_value=True)
        return bench

    def test_graceful_only_applied_to_frappe_container(self):
        bench = self._make_bench()

        bench.restart_web_containers_services(graceful=True)

        called_services = {
            call.args[0]: call.kwargs.get("graceful")
            for call in bench.restart_supervisor_service.call_args_list
        }

        assert called_services[SiteServicesEnum.frappe.value] is True
        assert called_services[SiteServicesEnum.socketio.value] is False

    def test_graceful_false_passes_false_to_both(self):
        bench = self._make_bench()

        bench.restart_web_containers_services(graceful=False)

        for call in bench.restart_supervisor_service.call_args_list:
            assert call.kwargs.get("graceful") is False

    def test_use_container_restart_bypasses_supervisor_path(self):
        bench = self._make_bench()

        bench.restart_web_containers_services(use_container_restart=True, graceful=True)

        bench.restart_supervisor_service.assert_not_called()
        bench.docker_ops.restart_services.assert_called_once()
