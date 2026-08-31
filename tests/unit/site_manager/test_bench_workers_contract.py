"""Characterization of the bench worker/schedule lifecycle (`bench_workers`).

Contracts pinned here, so a refactor of the module cannot silently change them:

1. **Which compose file each action targets.** All bench compose files share one
   directory and therefore one compose project, so aiming an action at the wrong
   file is destructive. `schedule` lives in the MAIN compose (reached via
   `docker_ops`), every worker lives in the WORKERS compose (reached via
   `workers.docker_client`). A test asserts the *other* client was never touched.
2. **`use_container_restart` vs the supervisor path.** Container restarts go
   through `compose restart` / `docker_ops.restart_services`; the supervisor path
   never touches containers, it cycles programs inside them.
3. **Force semantics.** `force` maps to `timeout=0` on container restarts
   (otherwise `100`), and to `restart_supervisor_service(..., force=True)`
   instead of the fmx SIGUSR1 cycle on the supervisor path.
4. **What is printed per branch.** Operators read these lines to know whether a
   container was recreated or only its programs were cycled, and the
   "Force restarted"/"Restarted" label is the only signal of which happened.
5. **The no-op guards.** No workers configured => only `schedule` is touched; no
   workers compose file => `ensure_workers_running_if_available` does nothing at
   all (it must not even ask whether the bench is running).

Docker is never reachable from these tests: the docker client, the compose
managers and `BackupManager` (which would otherwise mkdir under ~/frappe) are
mocked at their boundary and every file lives in `tmp_path`.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from frappe_manager import SiteServicesEnum
from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import BenchRuntime, WorkersConfig
from frappe_manager.site_manager.exceptions import (
    BenchOperationException,
    BenchWorkersSupervisorConfigurtionNotFoundError,
)
from frappe_manager.site_manager.modules.bench_workers import (
    FMX_BIN,
    BenchWorkerCoordinator,
    BenchWorkers,
)
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES

SCHEDULE = SiteServicesEnum.schedule.value
DEFAULT_WORKERS = ["long-worker", "short-worker"]

MODULE = "frappe_manager.site_manager.modules.bench_workers"


def _docker_exception(msg: str = "cannot connect to docker daemon") -> DockerException:
    return DockerException(["docker", "compose", "ps"], SubprocessOutput([], [msg], [msg], 1))


class _Harness:
    """A coordinator wired to mocks, with the two docker clients kept apart."""

    def __init__(
        self,
        tmp_path,
        worker_services=None,
        supervisor_restarted: bool = True,
        bench_running: bool = True,
        workers_config=None,
    ):
        self.output = MagicMock()
        self.workers = MagicMock()
        self.workers.compose_file_manager.get_services_list.return_value = list(
            DEFAULT_WORKERS if worker_services is None else worker_services
        )
        self.workers.bench.bench_config.workers = workers_config
        # WORKERS compose file
        self.workers_client = self.workers.docker_client
        self.docker_ops = MagicMock()
        # MAIN compose file
        self.main_client = self.docker_ops.docker_client
        self.supervisor = MagicMock()
        self.restart_supervisor = MagicMock(return_value=supervisor_restarted)
        self.is_running = MagicMock(return_value=bench_running)
        self.coordinator = BenchWorkerCoordinator(
            bench_name="test.localhost",
            workers=self.workers,
            supervisor=self.supervisor,
            bench_path=tmp_path / "test.localhost",
            restart_supervisor_service_fn=self.restart_supervisor,
            is_running_fn=self.is_running,
            docker_ops=self.docker_ops,
            output_handler=self.output,
        )

    @property
    def prints(self) -> list[str]:
        return [c.args[0] for c in self.output.print.call_args_list]

    @property
    def heads(self) -> list[str]:
        return [c.args[0] for c in self.output.change_head.call_args_list]

    @property
    def warnings(self) -> list[str]:
        return [c.args[0] for c in self.output.warning.call_args_list]


@pytest.fixture
def harness(tmp_path):
    def _make(**kwargs):
        return _Harness(tmp_path, **kwargs)

    return _make


# --------------------------------------------------------- container restart path


class TestRestartWorkersContainerPath:
    @pytest.mark.timeout(15)
    def test_schedule_goes_to_main_compose_and_workers_to_workers_compose(self, harness):
        h = harness()

        h.coordinator.restart_workers_containers_services(use_container_restart=True)

        # schedule: MAIN compose, via docker_ops
        h.docker_ops.restart_services.assert_called_once_with([SCHEDULE], force=False)
        # workers: WORKERS compose, one call per service
        assert [c.kwargs for c in h.workers_client.compose.restart.call_args_list] == [
            {"services": ["long-worker"], "timeout": 100},
            {"services": ["short-worker"], "timeout": 100},
        ]
        # the main compose client must never be asked to restart a worker
        h.main_client.compose.restart.assert_not_called()
        # container restarts never cycle supervisor programs
        h.restart_supervisor.assert_not_called()
        h.main_client.compose.exec.assert_not_called()
        h.workers_client.compose.exec.assert_not_called()

    @pytest.mark.timeout(15)
    def test_unforced_container_restart_uses_timeout_100_and_plain_label(self, harness):
        h = harness()

        h.coordinator.restart_workers_containers_services(use_container_restart=True, force=False)

        assert [c.kwargs["timeout"] for c in h.workers_client.compose.restart.call_args_list] == [100, 100]
        assert h.prints == [
            "Restarted container - long-worker",
            "Restarted container - short-worker",
        ]

    @pytest.mark.timeout(15)
    def test_forced_container_restart_uses_timeout_0_and_force_label(self, harness):
        h = harness()

        h.coordinator.restart_workers_containers_services(use_container_restart=True, force=True)

        assert [c.kwargs["timeout"] for c in h.workers_client.compose.restart.call_args_list] == [0, 0]
        assert h.prints == [
            "Force restarted container - long-worker",
            "Force restarted container - short-worker",
        ]
        # force is forwarded to the schedule restart too
        h.docker_ops.restart_services.assert_called_once_with([SCHEDULE], force=True)

    @pytest.mark.timeout(15)
    def test_only_workers_are_announced_in_the_container_path(self, harness):
        h = harness()

        h.coordinator.restart_workers_containers_services(use_container_restart=True)

        # schedule's progress line is owned by docker_ops.restart_services here
        assert h.heads == [
            "Restarting worker service - long-worker",
            "Restarting worker service - short-worker",
        ]


# --------------------------------------------------------- supervisor restart path


class TestRestartWorkersSupervisorForcePath:
    @pytest.mark.timeout(15)
    def test_force_uses_stop_start_per_service_on_the_matching_compose(self, harness):
        h = harness()

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=True)

        # schedule is restarted without an explicit client: the default (MAIN) compose
        assert h.restart_supervisor.call_args_list == [
            call(SCHEDULE, force=True),
            call("long-worker", docker_client_obj=h.workers_client, force=True),
            call("short-worker", docker_client_obj=h.workers_client, force=True),
        ]
        # no container is recreated or restarted on this path
        h.docker_ops.restart_services.assert_not_called()
        h.workers_client.compose.restart.assert_not_called()
        assert h.prints == [
            "Stopped and started supervisor processes - schedule",
            "Stopped and started supervisor processes - long-worker",
            "Stopped and started supervisor processes - short-worker",
        ]

    @pytest.mark.timeout(15)
    def test_force_prints_nothing_when_the_restart_reports_it_did_not_happen(self, harness):
        h = harness(supervisor_restarted=False)

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=True)

        assert h.restart_supervisor.call_count == 3
        assert h.prints == []

    @pytest.mark.timeout(15)
    def test_every_service_including_schedule_is_announced(self, harness):
        h = harness()

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=True)

        assert h.heads == [
            "Restarting worker service - schedule",
            "Restarting worker service - long-worker",
            "Restarting worker service - short-worker",
        ]


class TestRestartWorkersFmxCyclePath:
    @pytest.mark.timeout(15)
    def test_unforced_cycle_execs_fmx_in_each_service_on_its_own_compose(self, harness):
        h = harness()

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=False)

        # schedule cycles through the MAIN compose client (no client passed down)
        assert h.main_client.compose.exec.call_count == 1
        assert h.main_client.compose.exec.call_args.kwargs["service"] == SCHEDULE
        # workers cycle through the WORKERS compose client
        assert [c.kwargs["service"] for c in h.workers_client.compose.exec.call_args_list] == DEFAULT_WORKERS
        for c in h.workers_client.compose.exec.call_args_list:
            assert c.kwargs["user"] == "frappe"
            assert c.kwargs["stream"] is False
        # no container touched, no stop/start
        h.docker_ops.restart_services.assert_not_called()
        h.workers_client.compose.restart.assert_not_called()
        h.restart_supervisor.assert_not_called()
        assert h.prints == [
            "Restarted supervisor processes - schedule",
            "Restarted supervisor processes - long-worker",
            "Restarted supervisor processes - short-worker",
        ]

    @pytest.mark.timeout(15)
    def test_fmx_command_disables_its_own_drain_and_waits(self, harness):
        h = harness(workers_config=None)

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=False)

        cmd = h.workers_client.compose.exec.call_args_list[0].kwargs["command"]
        # a missing [workers] config falls back to WorkersConfig() defaults
        defaults = WorkersConfig()
        assert cmd == (
            f"{FMX_BIN} restart --no-drain-workers --wait"
            f" --worker-kill-timeout {defaults.kill_timeout}"
            f" --worker-kill-poll {defaults.kill_poll}"
        )

    @pytest.mark.timeout(15)
    def test_fmx_command_carries_the_configured_kill_ladder(self, harness):
        h = harness(workers_config=WorkersConfig(kill_timeout=42, kill_poll=0.25))

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=False)

        cmd = h.workers_client.compose.exec.call_args_list[0].kwargs["command"]
        assert "--worker-kill-timeout 42" in cmd
        assert "--worker-kill-poll 0.25" in cmd

    @pytest.mark.timeout(15)
    def test_missing_fmx_warns_and_falls_back_to_supervisorctl(self, harness):
        h = harness()
        h.main_client.compose.exec.side_effect = _docker_exception("executable not found")
        h.workers_client.compose.exec.side_effect = _docker_exception("executable not found")

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=False)

        assert h.warnings == [
            "fmx restart unavailable in schedule (old image?); falling back to supervisorctl",
            "fmx restart unavailable in long-worker (old image?); falling back to supervisorctl",
            "fmx restart unavailable in short-worker (old image?); falling back to supervisorctl",
        ]
        # the fallback is never the forceful stop+start
        assert h.restart_supervisor.call_args_list == [
            call(SCHEDULE, docker_client_obj=None, force=False),
            call("long-worker", docker_client_obj=h.workers_client, force=False),
            call("short-worker", docker_client_obj=h.workers_client, force=False),
        ]
        assert h.prints == [
            "Restarted supervisor processes - schedule",
            "Restarted supervisor processes - long-worker",
            "Restarted supervisor processes - short-worker",
        ]

    @pytest.mark.timeout(15)
    def test_fallback_that_restarts_nothing_warns_but_claims_no_restart(self, harness):
        h = harness(supervisor_restarted=False)
        h.main_client.compose.exec.side_effect = _docker_exception()
        h.workers_client.compose.exec.side_effect = _docker_exception()

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=False)

        assert len(h.warnings) == 3
        assert h.prints == []


class TestRestartWorkersNoWorkersConfigured:
    @pytest.mark.timeout(15)
    def test_container_path_restarts_schedule_only(self, harness):
        h = harness(worker_services=[])

        h.coordinator.restart_workers_containers_services(use_container_restart=True, force=True)

        h.docker_ops.restart_services.assert_called_once_with([SCHEDULE], force=True)
        h.workers_client.compose.restart.assert_not_called()
        assert h.prints == []
        assert h.heads == []

    @pytest.mark.timeout(15)
    def test_supervisor_path_cycles_schedule_only(self, harness):
        h = harness(worker_services=[])

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=False)

        h.main_client.compose.exec.assert_called_once()
        h.workers_client.compose.exec.assert_not_called()
        assert h.prints == ["Restarted supervisor processes - schedule"]


class TestRestartWorkersWithoutAComposeFile:
    """`fm start <bench> --reconfigure-workers --no-include-default-workers
    --no-include-custom-workers` unlinks docker-compose.workers.yml, so a bench can
    legitimately have no workers compose file. ComposeFile then falls back to the
    template, whose only service is the placeholder `worker-name`; restarting it is
    at best a bogus warning and at worst a DockerException that fails the command.
    These tests use a REAL ComposeFile (not a stubbed services list) so the template
    fallback is what is being guarded against.
    """

    @staticmethod
    def _coordinator(tmp_path):
        workers = _make_workers(tmp_path, confs=["long-worker.workers.fm.supervisor.conf"])
        assert not workers.compose_path.exists()
        # the state this guards against: the template's placeholder service
        assert workers.compose_file_manager.get_services_list() == ["worker-name"]

        output = MagicMock()
        restart_supervisor = MagicMock(return_value=True)
        docker_ops = MagicMock()
        coordinator = BenchWorkerCoordinator(
            bench_name="test.localhost",
            workers=workers,
            supervisor=MagicMock(),
            bench_path=tmp_path / "test.localhost",
            restart_supervisor_service_fn=restart_supervisor,
            is_running_fn=MagicMock(return_value=True),
            docker_ops=docker_ops,
            output_handler=output,
        )
        return SimpleNamespace(
            coordinator=coordinator,
            workers=workers,
            workers_client=workers.docker_client,
            docker_ops=docker_ops,
            output=output,
            restart_supervisor=restart_supervisor,
        )

    @pytest.mark.timeout(15)
    def test_container_path_restarts_schedule_only(self, tmp_path):
        h = self._coordinator(tmp_path)

        h.coordinator.restart_workers_containers_services(use_container_restart=True)

        h.docker_ops.restart_services.assert_called_once_with([SCHEDULE], force=False)
        # `compose restart worker-name` is an unhandled DockerException: it fails `fm restart --container`
        h.workers_client.compose.restart.assert_not_called()
        assert not [c.args[0] for c in h.output.change_head.call_args_list if "worker-name" in c.args[0]]

    @pytest.mark.timeout(15)
    def test_fmx_cycle_path_never_mentions_the_placeholder_service(self, tmp_path):
        h = self._coordinator(tmp_path)

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=False)

        h.workers_client.compose.exec.assert_not_called()
        assert not [c.args[0] for c in h.output.warning.call_args_list if "worker-name" in c.args[0]]
        assert [c.args[0] for c in h.output.print.call_args_list] == ["Restarted supervisor processes - schedule"]

    @pytest.mark.timeout(15)
    def test_force_path_cycles_schedules_supervisor_only(self, tmp_path):
        h = self._coordinator(tmp_path)

        h.coordinator.restart_workers_containers_services(use_container_restart=False, force=True)

        assert h.restart_supervisor.call_args_list == [call(SCHEDULE, force=True)]


# ------------------------------------------------ ensure_workers_running_if_available


class TestEnsureWorkersRunningIfAvailable:
    @staticmethod
    def _wire(h, services, statuses, container_names=None):
        cfm = h.workers.compose_file_manager
        cfm.exists.return_value = True
        cfm.get_services_list.return_value = list(services)
        cfm.get_container_names.return_value = container_names or {s: f"fm-test-{s}" for s in services}
        h.workers_client.compose.get_all_services_status.return_value = statuses

    @pytest.mark.timeout(15)
    def test_absent_compose_file_is_a_total_no_op(self, harness):
        h = harness()
        h.workers.compose_file_manager.exists.return_value = False

        h.coordinator.ensure_workers_running_if_available()

        h.workers_client.compose.get_all_services_status.assert_not_called()
        h.workers_client.compose.up.assert_not_called()
        # it must not even ask whether the bench is running
        h.is_running.assert_not_called()

    @pytest.mark.timeout(15)
    def test_all_workers_running_are_left_alone(self, harness):
        h = harness()
        self._wire(
            h,
            DEFAULT_WORKERS,
            [
                {"Name": "fm-test-long-worker", "Service": "long-worker", "State": "running"},
                {"Name": "fm-test-short-worker", "Service": "short-worker", "State": "running"},
            ],
        )

        h.coordinator.ensure_workers_running_if_available()

        h.workers_client.compose.up.assert_not_called()

    @pytest.mark.timeout(15)
    def test_one_stopped_worker_starts_the_workers_compose_without_recreating(self, harness):
        h = harness()
        self._wire(
            h,
            DEFAULT_WORKERS,
            [
                {"Name": "fm-test-long-worker", "Service": "long-worker", "State": "running"},
                {"Name": "fm-test-short-worker", "Service": "short-worker", "State": "exited"},
            ],
        )

        h.coordinator.ensure_workers_running_if_available()

        h.workers_client.compose.up.assert_called_once_with(
            services=[],
            detach=True,
            pull="never",
            force_recreate=False,
        )
        # never the main compose file
        h.main_client.compose.up.assert_not_called()

    @pytest.mark.timeout(15)
    def test_stopped_bench_is_not_started_just_to_run_workers(self, harness):
        h = harness(bench_running=False)
        self._wire(h, DEFAULT_WORKERS, [])

        h.coordinator.ensure_workers_running_if_available()

        h.is_running.assert_called_once_with()
        h.workers_client.compose.up.assert_not_called()

    @pytest.mark.timeout(15)
    def test_unreadable_status_is_treated_as_not_running(self, harness):
        h = harness()
        self._wire(h, DEFAULT_WORKERS, [])
        h.workers_client.compose.get_all_services_status.side_effect = _docker_exception()

        h.coordinator.ensure_workers_running_if_available()

        h.workers_client.compose.up.assert_called_once()

    @pytest.mark.timeout(15)
    def test_status_rows_of_other_containers_do_not_count_as_running(self, harness):
        h = harness()
        # a same-named service from another bench's compose project
        self._wire(
            h,
            ["long-worker"],
            [
                {"Name": "fm-other-long-worker", "Service": "long-worker", "State": "running"},
                {"Service": "long-worker", "State": "running"},
            ],
            container_names={"long-worker": "fm-test-long-worker"},
        )

        h.coordinator.ensure_workers_running_if_available()

        h.workers_client.compose.up.assert_called_once()


# ------------------------------------------------------------------ sync + backup


class TestSyncWorkersCompose:
    @pytest.mark.timeout(15)
    def test_unchanged_configuration_short_circuits_before_generating(self, harness):
        h = harness()
        h.workers.is_new_workers_added.return_value = True

        h.coordinator.sync_workers_compose(setup_supervisor=False)

        h.workers.generate_compose.assert_not_called()
        h.workers_client.compose.up.assert_not_called()
        assert h.prints == ["Workers configuration remains unchanged"]

    @pytest.mark.timeout(15)
    def test_changed_configuration_regenerates_and_starts_the_workers_compose(self, harness):
        h = harness()
        h.workers.is_new_workers_added.return_value = False
        h.workers.generate_compose.return_value = True

        h.coordinator.sync_workers_compose(setup_supervisor=False, force_recreate=True)

        h.workers.generate_compose.assert_called_once_with(
            include_default_workers=True,
            include_custom_workers=True,
        )
        h.workers_client.compose.up.assert_called_once_with(
            services=[],
            detach=True,
            pull="never",
            force_recreate=True,
        )
        h.main_client.compose.up.assert_not_called()

    @pytest.mark.timeout(15)
    def test_start_false_regenerates_without_starting(self, harness):
        h = harness()
        h.workers.is_new_workers_added.return_value = False
        h.workers.generate_compose.return_value = True

        h.coordinator.sync_workers_compose(setup_supervisor=False, start=False)

        h.workers.generate_compose.assert_called_once()
        h.workers_client.compose.up.assert_not_called()

    @pytest.mark.timeout(15)
    def test_no_workers_configured_is_never_started(self, harness):
        h = harness()
        h.workers.is_new_workers_added.return_value = False
        h.workers.generate_compose.return_value = False

        h.coordinator.sync_workers_compose(setup_supervisor=False)

        h.workers_client.compose.up.assert_not_called()

    @pytest.mark.timeout(15)
    def test_worker_flags_are_forwarded_to_both_checks(self, harness):
        h = harness()
        h.workers.is_new_workers_added.return_value = False
        h.workers.generate_compose.return_value = False

        h.coordinator.sync_workers_compose(
            setup_supervisor=False,
            include_default_workers=False,
            include_custom_workers=False,
        )

        h.workers.is_new_workers_added.assert_called_once_with(include_default_workers=False)
        h.workers.generate_compose.assert_called_once_with(
            include_default_workers=False,
            include_custom_workers=False,
        )

    @pytest.mark.timeout(15)
    def test_supervisor_setup_is_forced_and_backed_up_first(self, harness):
        h = harness()
        h.workers.is_new_workers_added.return_value = True
        with patch(f"{MODULE}.BackupManager") as backup_cls:
            h.workers.supervisor_config_path.exists.return_value = False
            h.coordinator.sync_workers_compose(setup_supervisor=True)

        backup_cls.assert_called_once_with(name="workers", backup_group_name="workers")
        h.supervisor.setup_supervisor.assert_called_once_with(h.coordinator.bench_path, force=True)

    @pytest.mark.timeout(15)
    def test_failed_supervisor_setup_rolls_back_and_re_raises(self, harness):
        h = harness()
        h.supervisor.setup_supervisor.side_effect = BenchOperationException("test.localhost", "bad workers config")
        with patch(f"{MODULE}.BackupManager") as backup_cls:
            manager = backup_cls.return_value
            manager.backups = ["conf-a", "conf-b"]
            h.workers.supervisor_config_path.exists.return_value = False

            with pytest.raises(BenchOperationException):
                h.coordinator.sync_workers_compose(setup_supervisor=True)

            assert manager.restore.call_args_list == [
                call("conf-a", force=True),
                call("conf-b", force=True),
            ]
        # a failed regen must never fall through to "unchanged" or to a start
        h.workers.generate_compose.assert_not_called()
        h.workers_client.compose.up.assert_not_called()
        assert "Rolling back to previous workers configuration" in h.prints


class TestBackupWorkersSupervisorConf:
    @pytest.mark.timeout(15)
    def test_existing_confs_are_backed_up_and_removed(self, harness, tmp_path):
        h = harness()
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        supervisor_conf = config_dir / "supervisor.conf"
        supervisor_conf.write_text("[supervisord]\n")
        worker_conf = config_dir / "long-worker.workers.fm.supervisor.conf"
        worker_conf.write_text("[program:x]\n")
        schedule_conf = config_dir / "schedule.fm.supervisor.conf"
        schedule_conf.write_text("[program:y]\n")
        keep = config_dir / "nginx.conf"
        keep.write_text("server {}\n")
        (config_dir / "subdir").mkdir()
        h.workers.config_dir = config_dir
        h.workers.supervisor_config_path = supervisor_conf

        with patch(f"{MODULE}.BackupManager") as backup_cls:
            manager = h.coordinator.backup_workers_supervisor_conf()

        assert manager is backup_cls.return_value
        backed_up = {c.args[0] for c in manager.backup.call_args_list}
        assert backed_up == {supervisor_conf, worker_conf, schedule_conf}
        assert all(c.kwargs == {"bench_name": "test.localhost"} for c in manager.backup.call_args_list)
        # the split confs are deleted so a regen cannot leave stale programs behind
        assert not worker_conf.exists()
        assert not schedule_conf.exists()
        # untouched: not a *.fm.supervisor.conf
        assert keep.exists()
        assert supervisor_conf.exists()

    @pytest.mark.timeout(15)
    def test_no_supervisor_conf_means_nothing_is_deleted(self, harness, tmp_path):
        h = harness()
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        worker_conf = config_dir / "long-worker.workers.fm.supervisor.conf"
        worker_conf.write_text("[program:x]\n")
        h.workers.config_dir = config_dir
        h.workers.supervisor_config_path = config_dir / "supervisor.conf"

        with patch(f"{MODULE}.BackupManager") as backup_cls:
            h.coordinator.backup_workers_supervisor_conf()

        manager = backup_cls.return_value
        assert manager.backup.call_count == 1
        assert worker_conf.exists()

    @pytest.mark.timeout(15)
    def test_regenerate_is_exactly_the_backup_and_delete_step(self, harness, tmp_path):
        h = harness()
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        supervisor_conf = config_dir / "supervisor.conf"
        supervisor_conf.write_text("[supervisord]\n")
        worker_conf = config_dir / "long-worker.workers.fm.supervisor.conf"
        worker_conf.write_text("[program:x]\n")
        h.workers.config_dir = config_dir
        h.workers.supervisor_config_path = supervisor_conf

        with patch(f"{MODULE}.BackupManager") as backup_cls:
            assert h.coordinator.regenerate_workers_supervisor_conf() is None

        assert backup_cls.return_value.backup.call_count == 2
        assert not worker_conf.exists()


# ------------------------------------------------------------------- BenchWorkers


def _bench_config(name="test.localhost", alias_domains=None, ssl_certificates=(), restart_policy="unless-stopped"):
    return SimpleNamespace(
        name=name,
        alias_domains=list(alias_domains or []),
        ssl_certificates=list(ssl_certificates),
        restart_policy=SimpleNamespace(value=restart_policy),
        runtime=BenchRuntime.mount,
        base_image=None,
        deploy_state=None,
        sites=None,
        redis=None,
        workers=None,
    )


def _make_workers(tmp_path, *, confs=(), common_config=None, bench_config=None) -> BenchWorkers:
    bench_path = tmp_path / "test.localhost"
    config_dir = bench_path / "workspace" / "frappe-bench" / "config"
    config_dir.mkdir(parents=True)
    for conf in confs:
        (config_dir / conf).write_text("[program:x]\n")
    bench = MagicMock()
    bench.path = bench_path
    # bench, site and domain are one string today; a mock that sets only `name` hands a MagicMock
    # to any caller that correctly asks for the site or the domain.
    bench.name = "test.localhost"
    bench.site_name = "test.localhost"
    bench.primary_domain = "test.localhost"
    bench.bench_config = bench_config or _bench_config()
    # Derived from the config the caller supplied, exactly as `Bench.domains` does, so a test that
    # passes alias domains gets them here instead of silently seeing only the primary.
    bench.domains = [bench.primary_domain, *(bench.bench_config.alias_domains or [])]
    bench.get_common_bench_config.return_value = {} if common_config is None else common_config
    workers = BenchWorkers(bench, output_handler=MagicMock())
    # never let a real docker client near a real daemon
    workers.docker_client = MagicMock()
    return workers


class TestGetExpectedWorkers:
    @pytest.mark.timeout(15)
    def test_no_worker_confs_raises_naming_the_bench_and_config_dir(self, tmp_path):
        workers = _make_workers(tmp_path, confs=["schedule.fm.supervisor.conf"])

        with pytest.raises(BenchWorkersSupervisorConfigurtionNotFoundError):
            workers.get_expected_workers()

    @pytest.mark.timeout(15)
    def test_names_are_stripped_and_sorted(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=[
                "short-worker.workers.fm.supervisor.conf",
                "long-worker.workers.fm.supervisor.conf",
                "frappe-bench-frappe-reports-worker.workers.fm.supervisor.conf",
            ],
        )

        assert workers.get_expected_workers() == ["long-worker", "reports-worker", "short-worker"]

    @pytest.mark.timeout(15)
    def test_default_workers_can_be_excluded(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=[
                "short-worker.workers.fm.supervisor.conf",
                "long-worker.workers.fm.supervisor.conf",
                "reports-worker.workers.fm.supervisor.conf",
            ],
        )

        assert workers.get_expected_workers(include_default_workers=False) == ["reports-worker"]

    @pytest.mark.timeout(15)
    def test_custom_workers_can_be_excluded(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=[
                "short-worker.workers.fm.supervisor.conf",
                "long-worker.workers.fm.supervisor.conf",
                "reports-worker.workers.fm.supervisor.conf",
            ],
        )

        assert workers.get_expected_workers(include_custom_workers=False) == ["long-worker", "short-worker"]


class TestIsNewWorkersAdded:
    @staticmethod
    def _with_compose(workers, services):
        workers.compose_path.write_text(
            "services:\n" + "".join(f"  {s}:\n    container_name: fm-test-{s}\n" for s in services)
        )
        from frappe_manager.docker import ComposeFile

        workers.compose_file_manager = ComposeFile(workers.compose_path, template_name="docker-compose.workers.tmpl")

    @pytest.mark.timeout(15)
    def test_missing_compose_file_always_reports_changed(self, tmp_path):
        workers = _make_workers(tmp_path, confs=["long-worker.workers.fm.supervisor.conf"])

        assert workers.compose_file_manager.is_template_loaded is True
        assert workers.is_new_workers_added() is False

    @pytest.mark.timeout(15)
    def test_matching_compose_and_confs_reports_unchanged(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=[
                "long-worker.workers.fm.supervisor.conf",
                "short-worker.workers.fm.supervisor.conf",
            ],
        )
        self._with_compose(workers, DEFAULT_WORKERS)

        assert workers.is_new_workers_added(include_default_workers=True) is True

    @pytest.mark.timeout(15)
    def test_queue_added_to_common_config_but_absent_from_compose_reports_changed(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=[
                "long-worker.workers.fm.supervisor.conf",
                "short-worker.workers.fm.supervisor.conf",
            ],
            common_config={"workers": {"reports": {"timeout": 300}}},
        )
        self._with_compose(workers, DEFAULT_WORKERS)

        assert workers.is_new_workers_added(include_default_workers=True) is False

    @pytest.mark.timeout(15)
    def test_queue_removed_from_common_config_but_present_in_compose_reports_changed(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=[
                "long-worker.workers.fm.supervisor.conf",
                "short-worker.workers.fm.supervisor.conf",
                "reports-worker.workers.fm.supervisor.conf",
            ],
            common_config={},
        )
        self._with_compose(workers, [*DEFAULT_WORKERS, "reports-worker"])

        assert workers.is_new_workers_added(include_default_workers=True) is False

    @pytest.mark.timeout(15)
    def test_malformed_workers_key_reports_changed(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=[
                "long-worker.workers.fm.supervisor.conf",
                "short-worker.workers.fm.supervisor.conf",
            ],
            common_config={"workers": "reports"},
        )
        self._with_compose(workers, DEFAULT_WORKERS)

        assert workers.is_new_workers_added(include_default_workers=True) is False


class TestGenerateCompose:
    @pytest.mark.timeout(15)
    def test_workers_are_written_with_identity_and_name_env(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=[
                "long-worker.workers.fm.supervisor.conf",
                "short-worker.workers.fm.supervisor.conf",
            ],
        )

        with patch(f"{MODULE}.get_proxy_ip_on_frontend", return_value=None):
            assert workers.generate_compose() is True

        assert workers.compose_path.exists()
        services = workers.compose_file_manager.yml["services"]
        assert sorted(services) == DEFAULT_WORKERS
        # the template's placeholder service never survives
        assert "worker-name" not in services
        for name in DEFAULT_WORKERS:
            env = services[name]["environment"]
            assert env["WORKER_NAME"] == name
            assert env["USERID"] == os.getuid()
            assert env["USERGROUP"] == os.getgid()
            assert "extra_hosts" not in services[name]

    @pytest.mark.timeout(15)
    def test_proxy_ip_pins_the_bench_and_alias_domains_as_extra_hosts(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=["long-worker.workers.fm.supervisor.conf"],
            bench_config=_bench_config(alias_domains=["alias.localhost"]),
        )

        with patch(f"{MODULE}.get_proxy_ip_on_frontend", return_value="10.0.0.9"):
            workers.generate_compose()

        assert workers.compose_file_manager.yml["services"]["long-worker"]["extra_hosts"] == [
            "test.localhost:10.0.0.9",
            "alias.localhost:10.0.0.9",
        ]

    @pytest.mark.timeout(15)
    def test_dev_ssl_mounts_the_ca_and_points_both_bundles_at_it(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=["long-worker.workers.fm.supervisor.conf"],
            bench_config=_bench_config(ssl_certificates=[SimpleNamespace(ssl_type=SUPPORTED_SSL_TYPES.dev)]),
        )
        services_dir = tmp_path / "services"
        ca = services_dir / "nginx-proxy" / "ssl" / "dev" / "ca" / "rootCA.pem"
        ca.parent.mkdir(parents=True)
        ca.write_text("-----BEGIN CERTIFICATE-----\n")

        with (
            patch(f"{MODULE}.get_proxy_ip_on_frontend", return_value=None),
            patch(f"{MODULE}.CLI_SERVICES_DIRECTORY", services_dir),
        ):
            workers.generate_compose()

        worker = workers.compose_file_manager.yml["services"]["long-worker"]
        assert f"{ca}:/etc/ssl/certs/fm-dev-ca.pem:ro" in worker["volumes"]
        assert worker["environment"]["NODE_EXTRA_CA_CERTS"] == "/etc/ssl/certs/fm-dev-ca.pem"
        assert worker["environment"]["REQUESTS_CA_BUNDLE"] == "/etc/ssl/certs/fm-dev-ca.pem"

    @pytest.mark.timeout(15)
    def test_missing_dev_ca_file_leaves_the_worker_untrusting(self, tmp_path):
        workers = _make_workers(
            tmp_path,
            confs=["long-worker.workers.fm.supervisor.conf"],
            bench_config=_bench_config(ssl_certificates=[SimpleNamespace(ssl_type=SUPPORTED_SSL_TYPES.dev)]),
        )

        with (
            patch(f"{MODULE}.get_proxy_ip_on_frontend", return_value=None),
            patch(f"{MODULE}.CLI_SERVICES_DIRECTORY", tmp_path / "no-services"),
        ):
            workers.generate_compose()

        env = workers.compose_file_manager.yml["services"]["long-worker"]["environment"]
        assert "NODE_EXTRA_CA_CERTS" not in env
        assert "REQUESTS_CA_BUNDLE" not in env

    @pytest.mark.timeout(15)
    def test_dropped_worker_is_removed_from_the_workers_compose_only(self, tmp_path):
        workers = _make_workers(tmp_path, confs=["long-worker.workers.fm.supervisor.conf"])
        workers.compose_path.write_text(
            "services:\n"
            "  long-worker:\n    container_name: fm-test-long-worker\n"
            "  reports-worker:\n    container_name: fm-test-reports-worker\n"
        )
        from frappe_manager.docker import ComposeFile

        workers.compose_file_manager = ComposeFile(workers.compose_path, template_name="docker-compose.workers.tmpl")

        with patch(f"{MODULE}.get_proxy_ip_on_frontend", return_value=None):
            workers.generate_compose()

        # targeted rm, never `--remove-orphans` (shared compose project)
        workers.docker_client.compose.rm.assert_called_once_with(
            services=["reports-worker"],
            stop=True,
            force=True,
            stream=False,
        )
        workers.docker_client.compose.down.assert_not_called()
        assert sorted(workers.compose_file_manager.yml["services"]) == ["long-worker"]

    @pytest.mark.timeout(15)
    def test_unremovable_dropped_container_warns_instead_of_failing(self, tmp_path):
        workers = _make_workers(tmp_path, confs=["long-worker.workers.fm.supervisor.conf"])
        workers.compose_path.write_text(
            "services:\n"
            "  long-worker:\n    container_name: fm-test-long-worker\n"
            "  reports-worker:\n    container_name: fm-test-reports-worker\n"
        )
        from frappe_manager.docker import ComposeFile

        workers.compose_file_manager = ComposeFile(workers.compose_path, template_name="docker-compose.workers.tmpl")
        workers.docker_client.compose.rm.side_effect = _docker_exception("no such container")

        with patch(f"{MODULE}.get_proxy_ip_on_frontend", return_value=None):
            assert workers.generate_compose() is True

        assert any("remove manually" in c.args[0] for c in workers.output.warning.call_args_list)

    @pytest.mark.timeout(15)
    def test_no_expected_workers_tears_down_and_deletes_the_compose_file(self, tmp_path):
        # confs exist (so the lookup does not raise) but every one is filtered out
        workers = _make_workers(
            tmp_path,
            confs=[
                "long-worker.workers.fm.supervisor.conf",
                "short-worker.workers.fm.supervisor.conf",
            ],
        )
        workers.compose_path.write_text("services:\n  long-worker:\n    container_name: fm-test-long-worker\n")
        from frappe_manager.docker import ComposeFile

        workers.compose_file_manager = ComposeFile(workers.compose_path, template_name="docker-compose.workers.tmpl")

        with patch(f"{MODULE}.get_proxy_ip_on_frontend", return_value=None):
            assert workers.generate_compose(include_default_workers=False) is False

        workers.docker_client.compose.down.assert_called_once_with(volumes=False, timeout=5, stream=True)
        assert not workers.compose_path.exists()

    @pytest.mark.timeout(15)
    def test_unreadable_existing_compose_drops_no_worker_containers(self, tmp_path):
        workers = _make_workers(tmp_path, confs=["long-worker.workers.fm.supervisor.conf"])
        # A compose file that exists but has no services key: listing it raises. This used to leak a
        # bare KeyError; it now raises ComposeFileException, because a truncated or hand-edited
        # compose is an operational condition and callers that tolerate it could not name a bare
        # KeyError without also swallowing real bugs. generate_compose catches Exception either way,
        # so the behaviour this test actually defends -- an unknown previous state is never read as
        # "everything was dropped" -- is unchanged.
        workers.compose_path.write_text("version: '3.9'\n")
        from frappe_manager.docker import ComposeFile
        from frappe_manager.docker.compose_exceptions import ComposeFileException

        workers.compose_file_manager = ComposeFile(workers.compose_path, template_name="docker-compose.workers.tmpl")
        with pytest.raises(ComposeFileException):
            workers.compose_file_manager.get_services_list()

        with patch(f"{MODULE}.get_proxy_ip_on_frontend", return_value=None):
            assert workers.generate_compose() is True

        # unknown previous state must never be read as "everything was dropped"
        workers.docker_client.compose.rm.assert_not_called()
        assert sorted(workers.compose_file_manager.yml["services"]) == ["long-worker"]
