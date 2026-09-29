"""A first install that cannot finish must leave the host as it found it, or be retryable.

The directory is what decides whether fm ever tries to create again, so a failure anywhere
between "make the directory" and "the stack is up" used to wedge the install permanently: the
next command skipped creation and replayed the same rejected `compose up`. Which step failed is
not enumerable -- a bound port, a taken container name, an overlapping subnet and an unreachable
registry all arrive as the same DockerException -- so the contract is about the transaction, not
about any one cause.
"""

from pathlib import Path
from unittest import mock

import pytest

from frappe_manager.services_manager.services import ServicesManager, _cause_line
from frappe_manager.services_manager.services_exceptions import ServicesNotCreated

SERVICES_MODULE = "frappe_manager.services_manager.services"


def make_manager(path: Path) -> ServicesManager:
    manager = ServicesManager(path=path, output_handler=mock.MagicMock())
    manager.compose_file_manager = mock.MagicMock()
    manager.compose_file_manager.get_services_list.return_value = ["mariadb", "nginx-proxy"]
    manager.docker_client = mock.MagicMock()
    manager.docker_client.network_ls.return_value = ["bridge"]
    return manager


@pytest.mark.unit
class TestTheFirstInstallIsOneTransaction:
    def test_a_failure_at_compose_up_is_reported_as_a_failed_creation(self, tmp_path):
        """`compose up` is inside the guard: it used to sit outside, so a daemon refusal escaped as
        a raw DockerException and the caller's rollback never ran."""
        manager = make_manager(tmp_path / "services")
        cause = RuntimeError("Error response from daemon: port is already allocated")
        manager.docker_client.compose.up.side_effect = cause

        with mock.patch.object(ServicesManager, "create"), pytest.raises(ServicesNotCreated) as excinfo:
            manager.entrypoint_checks(start=True)

        assert excinfo.value.__cause__ is cause

    def test_a_failure_at_pull_is_reported_the_same_way(self, tmp_path):
        """The pull sat outside the guard too, so an unreachable registry wedged the install."""
        manager = make_manager(tmp_path / "services")
        cause = RuntimeError("Error response from daemon: unauthorized")
        manager.docker_client.compose.pull.side_effect = cause

        with mock.patch.object(ServicesManager, "create"), pytest.raises(ServicesNotCreated) as excinfo:
            manager.entrypoint_checks(start=True)

        assert excinfo.value.__cause__ is cause

    def test_the_refusal_carries_the_daemons_own_sentence(self, tmp_path):
        """An operator can only act on the cause, and it is one line buried in a paragraph of
        invocation echo."""
        manager = make_manager(tmp_path / "services")
        manager.docker_client.compose.up.side_effect = RuntimeError(
            "The docker command executed was `docker compose up`.\nIt returned with code 1\n"
            "Error response from daemon: invalid pool request: Pool overlaps with other one"
        )

        with mock.patch.object(ServicesManager, "create"), pytest.raises(ServicesNotCreated) as excinfo:
            manager.entrypoint_checks(start=True)

        assert "Pool overlaps with other one" in str(excinfo.value)


@pytest.mark.unit
class TestTheRollbackIsComplete:
    def test_networks_created_by_this_run_are_removed(self, tmp_path):
        """Deleting the directory alone leaves docker objects no config describes, which the next
        attempt then collides with."""
        manager = make_manager(tmp_path / "services")
        manager.docker_client.network_ls.side_effect = [
            ["bridge"],
            ["bridge", "fm-frontend-network", "fm-backend-network"],
        ]
        manager.docker_client.compose.up.side_effect = RuntimeError("boom")

        with mock.patch.object(ServicesManager, "create"), pytest.raises(ServicesNotCreated):
            manager.entrypoint_checks(start=True)
        with mock.patch(f"{SERVICES_MODULE}.FMConfigManager"):
            manager.remove_itself()

        removed = [call.args[0] for call in manager.docker_client.network_rm.call_args_list]
        assert sorted(removed) == ["fm-backend-network", "fm-frontend-network"]

    def test_a_network_that_predates_this_run_is_never_removed(self, tmp_path):
        """The shared names are identical on a host that already had a working install; removing
        one under a live bench would take it down."""
        manager = make_manager(tmp_path / "services")
        manager.docker_client.network_ls.side_effect = [
            ["bridge", "fm-frontend-network"],
            ["bridge", "fm-frontend-network"],
        ]
        manager.docker_client.compose.up.side_effect = RuntimeError("boom")

        with mock.patch.object(ServicesManager, "create"), pytest.raises(ServicesNotCreated):
            manager.entrypoint_checks(start=True)
        with mock.patch(f"{SERVICES_MODULE}.FMConfigManager"):
            manager.remove_itself()

        manager.docker_client.network_rm.assert_not_called()

    def test_the_recorded_subnets_are_cleared(self, tmp_path):
        """A subnet written by the failed run would otherwise be replayed by every attempt after
        it, including the one that failed to create the network."""
        manager = make_manager(tmp_path / "services")
        manager.docker_client.compose.up.side_effect = RuntimeError("boom")

        with mock.patch.object(ServicesManager, "create"), pytest.raises(ServicesNotCreated):
            manager.entrypoint_checks(start=True)
        with mock.patch(f"{SERVICES_MODULE}.FMConfigManager") as config_cls:
            fm_config = config_cls.import_from_toml.return_value
            manager.remove_itself()

        assert fm_config.network.subnet_cidr is None
        assert fm_config.network.backend_subnet_cidr is None
        fm_config.export_to_toml.assert_called_once()

    def test_an_existing_install_keeps_its_recorded_network(self, tmp_path):
        """remove_itself also serves paths that did not create this install; clearing the network
        table there would renumber a stack whose containers are attached to it."""
        services_path = tmp_path / "services"
        services_path.mkdir(parents=True)
        manager = make_manager(services_path)

        with mock.patch(f"{SERVICES_MODULE}.FMConfigManager") as config_cls:
            manager.remove_itself()

        config_cls.import_from_toml.assert_not_called()


@pytest.mark.unit
class TestTheRetryHeals:
    def _manager_with_compose(self, tmp_path) -> ServicesManager:
        services_path = tmp_path / "services"
        services_path.mkdir(parents=True)
        (services_path / "docker-compose.yml").write_text("services: {}\n")
        manager = make_manager(services_path)
        return manager

    def test_a_stack_that_never_started_is_revalidated_before_being_replayed(self, tmp_path):
        """Fix the cause, run again: without this the rendered compose (and its rejected subnet)
        is replayed forever, because creation is skipped once the directory exists."""
        manager = self._manager_with_compose(tmp_path)
        manager.docker_client.compose.get_all_services_status.return_value = []

        with mock.patch.object(ServicesManager, "configure_shared_networks") as configure:
            manager.heal_unstarted_stack()

        configure.assert_called_once()
        manager.compose_file_manager.write_to_file.assert_called_once()

    def test_a_stack_with_containers_is_left_alone(self, tmp_path):
        """A stopped-but-created stack is a deliberate state (`fm services stop`); re-rendering
        would renumber networks its containers are attached to."""
        manager = self._manager_with_compose(tmp_path)
        manager.docker_client.compose.get_all_services_status.return_value = [
            {"Name": "fm_mariadb", "Service": "mariadb", "State": "exited"}
        ]

        with mock.patch.object(ServicesManager, "configure_shared_networks") as configure:
            manager.heal_unstarted_stack()

        configure.assert_not_called()


@pytest.mark.unit
class TestTheCauseLine:
    def test_the_daemons_sentence_is_picked_out_of_the_invocation_echo(self):
        """DockerException carries the whole command and its stderr; only the last error line
        tells an operator what to fix."""
        error = RuntimeError(
            "The docker command executed was `docker compose -f x up`.\n"
            "It returned with code 1\n"
            "' Network fm-backend-network  Error\nError response from daemon: invalid pool request'"
        )

        assert _cause_line(error) == "Error response from daemon: invalid pool request"

    def test_an_error_with_no_recognisable_line_still_yields_something(self):
        assert _cause_line(RuntimeError("")) == "RuntimeError"
