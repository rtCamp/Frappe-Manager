"""
Defends the pre-migration global-services check inside ``MigrationOrchestrator``.

``execute_migrations()`` starts by making sure the global services stack is up. The
contract that check must honour:

1. A genuine services/docker/compose/filesystem failure is TOLERATED. The operator gets
   the exact same warning as always ("Warning: Could not verify/start global services...
   Try manually: fm services start", ``:warning:``), the cause is logged with its
   traceback, and the migration continues.
2. A programming error raised inside that block (AttributeError, TypeError, ImportError,
   KeyError) PROPAGATES. Relabelling it as a services problem would hide a real bug and
   send the operator to ``fm services start`` for something unrelated to services.

Everything is driven through the public ``execute_migrations()`` entry point, and the
``ServicesManager`` boundary is stubbed, so no test can reach docker or the real
services directory.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, Mock, patch

import pytest
from ruamel.yaml import YAMLError

from frappe_manager.docker.compose_exceptions import ComposeSecretNotFoundError, ComposeServiceNotFound
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.migration_manager.migration_orchestrator import MigrationOrchestrator
from frappe_manager.migration_manager.version import Version
from frappe_manager.services_manager.services_exceptions import (
    DatabaseServicePasswordNotFound,
    ServicesComposeNotExist,
    ServicesNotCreated,
)

EXPECTED_WARNING = (
    "Warning: Could not verify/start global services. "
    "Migration may fail if services are not running. "
    "Try manually: fm services start"
)


def docker_failure() -> DockerException:
    """A DockerException shaped like a real `docker compose up` failure."""
    return DockerException(
        ["docker", "compose", "up", "-d"],
        SubprocessOutput(stdout=[], stderr=["cannot connect to the docker daemon"], combined=[], exit_code=1),
    )


def make_orchestrator(migrations=()):
    """An orchestrator whose executor is fully stubbed; nothing touches disk or docker."""
    executor = MagicMock()
    executor.output = MagicMock()
    executor.migrations = list(migrations)
    orchestrator = MigrationOrchestrator(executor)
    orchestrator.logger = Mock()
    return orchestrator


@contextmanager
def stub_services(**attrs):
    """Replace the ServicesManager the orchestrator imports lazily at call time.

    Defaults describe an installed-but-stopped services stack, so the check walks all
    the way to ``start_service()``.
    """
    with patch("frappe_manager.services_manager.services.ServicesManager") as services_cls:
        services = services_cls.return_value
        services.path.exists.return_value = True
        services.compose_file_manager.get_services_list.return_value = ["global-db"]
        services.is_service_running.return_value = False
        for name, value in attrs.items():
            setattr(services, name, value)
        yield services


def warning_calls(output):
    return [call for call in output.print.call_args_list if call.args and call.args[0] == EXPECTED_WARNING]


class TestToleratedServiceFailures:
    @pytest.mark.timeout(15)
    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(docker_failure(), id="DockerException"),
            pytest.param(ServicesNotCreated("could not create global services"), id="ServicesNotCreated"),
            pytest.param(ServicesComposeNotExist("no compose file"), id="ServicesComposeNotExist"),
            pytest.param(DatabaseServicePasswordNotFound("global-db"), id="DatabaseServicePasswordNotFound"),
            pytest.param(ComposeServiceNotFound("global-nginx-proxy"), id="ComposeServiceNotFound"),
            pytest.param(
                ComposeSecretNotFoundError("db_root_password", "services/docker-compose.yml"),
                id="ComposeSecretNotFound",
            ),
            pytest.param(YAMLError("compose file is not valid yaml"), id="YAMLError"),
            pytest.param(PermissionError(13, "Permission denied"), id="OSError"),
        ],
    )
    def test_service_failure_warns_and_migration_proceeds(self, failure):
        """Each tolerated failure produces the identical warning and does not abort."""
        orchestrator = make_orchestrator()

        with stub_services(start_service=Mock(side_effect=failure)) as services:
            assert orchestrator.execute_migrations() is True

        services.start_service.assert_called_once_with()

        matched = warning_calls(orchestrator.executor.output)
        assert len(matched) == 1
        assert matched[0].kwargs["emoji_code"] == ":warning:"

    @pytest.mark.timeout(15)
    def test_tolerated_failure_is_logged_with_traceback(self):
        """The log keeps the original message AND the traceback of the real cause."""
        orchestrator = make_orchestrator()

        with stub_services(start_service=Mock(side_effect=docker_failure())):
            orchestrator.execute_migrations()

        orchestrator.logger.error.assert_called_once()
        logged = orchestrator.logger.error.call_args.args[0]
        assert logged.startswith("Failed to ensure global services are running: ")
        assert "Traceback (most recent call last)" in logged
        assert "DockerException" in logged
        # The frame that actually raised is identifiable from the log.
        assert "start_service" in logged.split("Traceback (most recent call last)", 1)[1]

    @pytest.mark.timeout(15)
    def test_warning_is_wrapped_in_temporary_stop(self):
        """The spinner is stopped around the warning and restored afterwards."""
        orchestrator = make_orchestrator()
        output = orchestrator.executor.output
        output.configure_mock(is_spinner_active=True, _current_text="Checking global services")

        with stub_services(start_service=Mock(side_effect=docker_failure())):
            orchestrator.execute_migrations()

        output.stop.assert_called()
        output.start.assert_called_with("Checking global services")

    @pytest.mark.timeout(15)
    def test_migration_still_runs_after_tolerated_failure(self):
        """A docker failure in the pre-check does not stop the migration loop."""
        migration = Mock()
        migration.version = Version("0.19.0")
        migration.get_rollback_version = Mock(return_value=Version("0.19.0"))

        orchestrator = make_orchestrator([migration])

        with stub_services(start_service=Mock(side_effect=docker_failure())):
            assert orchestrator.execute_migrations() is True

        migration.up.assert_called_once_with()
        assert len(warning_calls(orchestrator.executor.output)) == 1

    @pytest.mark.timeout(15)
    def test_uninitialized_services_failure_is_also_tolerated(self):
        """The 'services not initialized' branch warns and continues too."""
        orchestrator = make_orchestrator()

        with stub_services(entrypoint_checks=Mock(side_effect=ServicesNotCreated("cannot create"))) as services:
            services.path.exists.return_value = False
            assert orchestrator.execute_migrations() is True

        services.entrypoint_checks.assert_called_once_with(start=True)
        assert len(warning_calls(orchestrator.executor.output)) == 1


class TestProgrammingErrorsPropagate:
    @pytest.mark.timeout(15)
    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(AttributeError("'ServicesManager' object has no attribute 'start_service'"), id="Attribute"),
            pytest.param(TypeError("start_service() takes 1 positional argument but 2 were given"), id="Type"),
            pytest.param(ImportError("cannot import name 'MariaDBManager'"), id="Import"),
            pytest.param(KeyError("services"), id="Key"),
        ],
    )
    def test_programming_error_propagates_and_never_warns(self, failure):
        """A bug in our own code escapes instead of being reported as a services problem."""
        orchestrator = make_orchestrator()

        with (
            stub_services(start_service=Mock(side_effect=failure)),
            pytest.raises(type(failure)) as raised,
        ):
            orchestrator.execute_migrations()

        assert raised.value is failure
        assert warning_calls(orchestrator.executor.output) == []
        orchestrator.logger.error.assert_not_called()

    @pytest.mark.timeout(15)
    def test_programming_error_aborts_before_any_migration_runs(self):
        """execute_migrations() surfaces the bug rather than limping on."""
        migration = Mock()
        migration.version = Version("0.19.0")

        orchestrator = make_orchestrator([migration])
        boom = AttributeError("'NoneType' object has no attribute 'compose'")

        with (
            stub_services(is_service_running=Mock(side_effect=boom)),
            pytest.raises(AttributeError),
        ):
            orchestrator.execute_migrations()

        migration.up.assert_not_called()
        assert warning_calls(orchestrator.executor.output) == []
