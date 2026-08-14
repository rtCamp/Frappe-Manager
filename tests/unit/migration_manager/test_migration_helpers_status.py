"""Characterization tests for MigrationBench's service-status logic.

These pin the CURRENT behaviour of ``MigrationBench.compose``, ``MigrationBench.running``,
``MigrationBench.workers_running`` and ``MigrationBench.get_services_running_status``.

The compose/docker seam is mocked at construction time (``ComposeFile`` / ``DockerClient`` are
patched in the module under test), so no docker daemon is ever contacted.
"""

from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.migration_manager.migration_helpers import MigrationBench


def docker_exception() -> DockerException:
    """A real DockerException, as raised by the docker compose wrapper when the daemon fails."""
    return DockerException(
        ["docker", "compose", "ps", "--format", "json"],
        SubprocessOutput(
            stdout=[],
            stderr=["Cannot connect to the Docker daemon"],
            combined=["Cannot connect to the Docker daemon"],
            exit_code=1,
        ),
    )


@pytest.fixture
def bench(mock_bench_path):
    """A MigrationBench whose compose-file managers and docker clients are MagicMocks.

    Four distinct mocks are handed out in construction order: main compose file, workers compose
    file, main docker client, workers docker client.
    """
    with (
        patch("frappe_manager.migration_manager.migration_helpers.ComposeFile") as compose_file_cls,
        patch("frappe_manager.migration_manager.migration_helpers.DockerClient") as docker_client_cls,
    ):
        compose_file_cls.side_effect = lambda *args, **kwargs: MagicMock()
        docker_client_cls.side_effect = lambda *args, **kwargs: MagicMock()
        yield MigrationBench("test-bench", mock_bench_path)


def program_main(bench, services, containers, statuses=None, raises=None):
    """Point the main compose seam at a fixed service list, container map and `docker compose ps`."""
    bench.compose_file_manager.get_services_list.return_value = list(services)
    bench.compose_file_manager.get_container_names.return_value = dict(containers)
    if raises is not None:
        bench.docker.compose.get_all_services_status.side_effect = raises
    else:
        bench.docker.compose.get_all_services_status.return_value = list(statuses or [])


def program_workers(bench, services, containers, statuses=None, raises=None):
    """Same, for the workers compose seam."""
    bench.workers_compose_file_manager.get_services_list.return_value = list(services)
    bench.workers_compose_file_manager.get_container_names.return_value = dict(containers)
    if raises is not None:
        bench.workers_docker.compose.get_all_services_status.side_effect = raises
    else:
        bench.workers_docker.compose.get_all_services_status.return_value = list(statuses or [])


TWO_SERVICES = ["frappe", "nginx"]
TWO_CONTAINERS = {"frappe": "test-bench-frappe", "nginx": "test-bench-nginx"}


class TestComposeProperty:
    def test_compose_returns_the_docker_clients_compose(self, bench):
        assert bench.compose is bench.docker.compose

    def test_compose_asserts_when_docker_client_has_no_compose(self, bench):
        bench.docker.compose = None

        with pytest.raises(AssertionError):
            _ = bench.compose


class TestGetServicesRunningStatus:
    def test_maps_service_name_to_state(self, bench):
        program_main(
            bench,
            TWO_SERVICES,
            TWO_CONTAINERS,
            statuses=[
                {"Service": "frappe", "Name": "test-bench-frappe", "State": "running"},
                {"Service": "nginx", "Name": "test-bench-nginx", "State": "exited"},
            ],
        )

        assert bench.get_services_running_status() == {"frappe": "running", "nginx": "exited"}

    def test_ignores_containers_belonging_to_other_projects(self, bench):
        program_main(
            bench,
            TWO_SERVICES,
            TWO_CONTAINERS,
            statuses=[
                {"Service": "frappe", "Name": "test-bench-frappe", "State": "running"},
                {"Service": "frappe", "Name": "other-bench-frappe", "State": "running"},
            ],
        )

        assert bench.get_services_running_status() == {"frappe": "running"}

    def test_ignores_status_entries_without_a_name(self, bench):
        program_main(
            bench,
            TWO_SERVICES,
            TWO_CONTAINERS,
            statuses=[
                {"Service": "frappe", "State": "running"},
                {"Service": "nginx", "Name": "test-bench-nginx", "State": "running"},
            ],
        )

        assert bench.get_services_running_status() == {"nginx": "running"}

    def test_returns_empty_dict_when_no_status_matches_the_containers(self, bench):
        program_main(
            bench,
            TWO_SERVICES,
            TWO_CONTAINERS,
            statuses=[{"Service": "frappe", "Name": "other-bench-frappe", "State": "running"}],
        )

        assert bench.get_services_running_status() == {}

    def test_returns_empty_dict_when_docker_raises_docker_exception(self, bench):
        program_main(bench, TWO_SERVICES, TWO_CONTAINERS, raises=docker_exception())

        assert bench.get_services_running_status() == {}

    def test_does_not_swallow_non_docker_errors(self, bench):
        program_main(bench, TWO_SERVICES, TWO_CONTAINERS, raises=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            bench.get_services_running_status()


class TestRunning:
    def test_false_when_status_map_is_empty(self, bench):
        program_main(bench, TWO_SERVICES, TWO_CONTAINERS, statuses=[])

        assert bench.running is False

    def test_false_when_docker_raises_docker_exception(self, bench):
        program_main(bench, TWO_SERVICES, TWO_CONTAINERS, raises=docker_exception())

        assert bench.running is False

    def test_false_when_a_service_is_missing_from_a_non_empty_status_map(self, bench):
        program_main(
            bench,
            TWO_SERVICES,
            TWO_CONTAINERS,
            statuses=[{"Service": "frappe", "Name": "test-bench-frappe", "State": "running"}],
        )

        assert bench.running is False

    def test_false_when_a_service_reports_a_state_other_than_running(self, bench):
        program_main(
            bench,
            TWO_SERVICES,
            TWO_CONTAINERS,
            statuses=[
                {"Service": "frappe", "Name": "test-bench-frappe", "State": "running"},
                {"Service": "nginx", "Name": "test-bench-nginx", "State": "exited"},
            ],
        )

        assert bench.running is False

    def test_false_when_a_service_is_only_restarting(self, bench):
        program_main(
            bench,
            TWO_SERVICES,
            TWO_CONTAINERS,
            statuses=[
                {"Service": "frappe", "Name": "test-bench-frappe", "State": "restarting"},
                {"Service": "nginx", "Name": "test-bench-nginx", "State": "running"},
            ],
        )

        assert bench.running is False

    def test_true_when_every_service_reports_running(self, bench):
        program_main(
            bench,
            TWO_SERVICES,
            TWO_CONTAINERS,
            statuses=[
                {"Service": "frappe", "Name": "test-bench-frappe", "State": "running"},
                {"Service": "nginx", "Name": "test-bench-nginx", "State": "running"},
            ],
        )

        assert bench.running is True

    def test_ignores_extra_running_services_not_in_the_compose_service_list(self, bench):
        program_main(
            bench,
            ["frappe"],
            TWO_CONTAINERS,
            statuses=[
                {"Service": "frappe", "Name": "test-bench-frappe", "State": "running"},
                {"Service": "nginx", "Name": "test-bench-nginx", "State": "exited"},
            ],
        )

        assert bench.running is True

    def test_true_when_there_are_no_services_but_the_status_map_is_not_empty(self, bench):
        program_main(
            bench,
            [],
            TWO_CONTAINERS,
            statuses=[{"Service": "frappe", "Name": "test-bench-frappe", "State": "running"}],
        )

        assert bench.running is True

    def test_false_when_there_are_no_services_and_the_status_map_is_empty(self, bench):
        # The empty-map guard wins over the (vacuously true) service loop -- this is exactly where
        # `running` and `workers_running` disagree.
        program_main(bench, [], TWO_CONTAINERS, statuses=[])

        assert bench.running is False


class TestWorkersRunning:
    def test_true_when_every_worker_reports_running(self, bench):
        program_workers(
            bench,
            ["frappe-schedule", "frappe-short-worker"],
            {"frappe-schedule": "test-bench-schedule", "frappe-short-worker": "test-bench-short"},
            statuses=[
                {"Service": "frappe-schedule", "Name": "test-bench-schedule", "State": "running"},
                {"Service": "frappe-short-worker", "Name": "test-bench-short", "State": "running"},
            ],
        )

        assert bench.workers_running is True

    def test_false_when_a_worker_is_missing_from_the_status_map(self, bench):
        program_workers(
            bench,
            ["frappe-schedule", "frappe-short-worker"],
            {"frappe-schedule": "test-bench-schedule", "frappe-short-worker": "test-bench-short"},
            statuses=[{"Service": "frappe-schedule", "Name": "test-bench-schedule", "State": "running"}],
        )

        assert bench.workers_running is False

    def test_false_when_a_worker_reports_a_state_other_than_running(self, bench):
        program_workers(
            bench,
            ["frappe-schedule"],
            {"frappe-schedule": "test-bench-schedule"},
            statuses=[{"Service": "frappe-schedule", "Name": "test-bench-schedule", "State": "exited"}],
        )

        assert bench.workers_running is False

    def test_false_when_docker_raises_docker_exception(self, bench):
        program_workers(
            bench,
            ["frappe-schedule"],
            {"frappe-schedule": "test-bench-schedule"},
            raises=docker_exception(),
        )

        assert bench.workers_running is False

    def test_false_when_a_status_entry_is_missing_its_state_key(self, bench):
        program_workers(
            bench,
            ["frappe-schedule"],
            {"frappe-schedule": "test-bench-schedule"},
            statuses=[{"Service": "frappe-schedule", "Name": "test-bench-schedule"}],
        )

        assert bench.workers_running is False

    def test_true_when_there_are_no_workers_and_the_status_map_is_empty(self, bench):
        # Unlike `running`, workers_running has no empty-map guard, so an empty workers compose file
        # counts as running.
        program_workers(bench, [], {}, statuses=[])

        assert bench.workers_running is True

    def test_does_not_swallow_non_docker_errors(self, bench):
        program_workers(
            bench,
            ["frappe-schedule"],
            {"frappe-schedule": "test-bench-schedule"},
            raises=RuntimeError("boom"),
        )

        with pytest.raises(RuntimeError):
            _ = bench.workers_running
