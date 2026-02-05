"""
Tests for DockerComposeWrapper logging integration.

Verifies that DockerComposeWrapper properly uses ContextualLogger with structured
extra fields for all Docker Compose operations.
"""

from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.docker.docker_compose import DockerComposeWrapper
from frappe_manager.logger.contextual import ContextualLogger


@pytest.fixture
def mock_contextual_logger():
    """Create a mock ContextualLogger for testing."""
    logger = MagicMock(spec=ContextualLogger)
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.exception = MagicMock()
    return logger


@pytest.fixture
def temp_compose_file(tmp_path):
    """Create a temporary docker-compose.yml file for testing."""
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("version: '3'\nservices:\n  frappe:\n    image: frappe:latest\n")
    return compose_file


@pytest.fixture
def docker_compose_wrapper(temp_compose_file, mock_contextual_logger):
    """Create a DockerComposeWrapper instance with mock logger."""
    return DockerComposeWrapper(
        path=temp_compose_file,
        timeout=100,
        output=None,
        logger=mock_contextual_logger,
    )


class TestDockerComposeWrapperLoggerStorage:
    """Test logger parameter storage and fallback."""

    def test_stores_provided_logger(self, temp_compose_file, mock_contextual_logger):
        """Verify DockerComposeWrapper stores provided logger."""
        wrapper = DockerComposeWrapper(
            path=temp_compose_file,
            logger=mock_contextual_logger,
        )
        assert wrapper.logger is mock_contextual_logger

    def test_creates_fallback_logger_when_not_provided(self, temp_compose_file, mocker):
        """Verify DockerComposeWrapper creates default logger if not provided."""
        mock_get_logger = mocker.patch("frappe_manager.logger.log.get_logger")
        mock_logger_instance = MagicMock()
        mock_get_logger.return_value = mock_logger_instance

        wrapper = DockerComposeWrapper(
            path=temp_compose_file,
            logger=None,
        )

        mock_get_logger.assert_called_once()
        assert wrapper.logger is mock_logger_instance


class TestUpCommandLogging:
    """Test logging in up() command."""

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_up_logs_with_extra_fields(self, mock_run, docker_compose_wrapper):
        """Verify up() logs with services and detach flag."""
        mock_run.return_value = MagicMock(exit_code=0)

        docker_compose_wrapper.up(services=["frappe", "nginx"], detach=True, build=False)

        # Verify logger.debug was called with structured extra
        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        assert len(debug_calls) > 0

        # Check for the operation log
        found_up_log = False
        for call_obj in debug_calls:
            if "docker_up" in str(call_obj):
                found_up_log = True
                args, kwargs = call_obj
                if "extra" in kwargs:
                    extra = kwargs["extra"]
                    assert extra.get("operation") == "docker_up"
                    assert extra.get("services") == ["frappe", "nginx"]
                    assert extra.get("detach") is True

        assert found_up_log, "No docker_up operation logged"

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_up_logs_build_flag(self, mock_run, docker_compose_wrapper):
        """Verify up() logs build flag."""
        mock_run.return_value = MagicMock(exit_code=0)

        docker_compose_wrapper.up(build=True, detach=False)

        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        assert any(
            "docker_up" in str(call_obj) and call_obj[1].get("extra", {}).get("build") is True
            for call_obj in debug_calls
        )


class TestDownCommandLogging:
    """Test logging in down() command."""

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_down_logs_with_extra_fields(self, mock_run, docker_compose_wrapper):
        """Verify down() logs with volumes flag."""
        mock_run.return_value = MagicMock(exit_code=0)

        docker_compose_wrapper.down(volumes=True, timeout=60)

        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        found_down_log = False
        for call_obj in debug_calls:
            if "docker_down" in str(call_obj):
                found_down_log = True
                args, kwargs = call_obj
                if "extra" in kwargs:
                    extra = kwargs["extra"]
                    assert extra.get("operation") == "docker_down"
                    assert extra.get("volumes") is True

        assert found_down_log, "No docker_down operation logged"


class TestStartCommandLogging:
    """Test logging in start() command."""

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_start_logs_with_services(self, mock_run, docker_compose_wrapper):
        """Verify start() logs with service names."""
        mock_run.return_value = MagicMock(exit_code=0)

        docker_compose_wrapper.start(services=["frappe", "nginx"])

        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        found_start_log = False
        for call_obj in debug_calls:
            if "docker_start" in str(call_obj):
                found_start_log = True
                args, kwargs = call_obj
                if "extra" in kwargs:
                    extra = kwargs["extra"]
                    assert extra.get("operation") == "docker_start"
                    assert extra.get("services") == ["frappe", "nginx"]

        assert found_start_log, "No docker_start operation logged"

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_start_logs_with_none_services(self, mock_run, docker_compose_wrapper):
        """Verify start() logs when services is None."""
        mock_run.return_value = MagicMock(exit_code=0)

        docker_compose_wrapper.start(services=None)

        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        assert any("docker_start" in str(call_obj) for call_obj in debug_calls)


class TestStopCommandLogging:
    """Test logging in stop() command."""

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_stop_logs_with_services(self, mock_run, docker_compose_wrapper):
        """Verify stop() logs with service names."""
        mock_run.return_value = MagicMock(exit_code=0)

        docker_compose_wrapper.stop(services=["frappe"])

        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        found_stop_log = False
        for call_obj in debug_calls:
            if "docker_stop" in str(call_obj):
                found_stop_log = True
                args, kwargs = call_obj
                if "extra" in kwargs:
                    extra = kwargs["extra"]
                    assert extra.get("operation") == "docker_stop"
                    assert extra.get("services") == ["frappe"]

        assert found_stop_log, "No docker_stop operation logged"


class TestRestartCommandLogging:
    """Test logging in restart() command."""

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_restart_logs_with_services(self, mock_run, docker_compose_wrapper):
        """Verify restart() logs with service names."""
        mock_run.return_value = MagicMock(exit_code=0)

        docker_compose_wrapper.restart(services=["nginx"])

        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        found_restart_log = False
        for call_obj in debug_calls:
            if "docker_restart" in str(call_obj):
                found_restart_log = True
                args, kwargs = call_obj
                if "extra" in kwargs:
                    extra = kwargs["extra"]
                    assert extra.get("operation") == "docker_restart"
                    assert extra.get("services") == ["nginx"]

        assert found_restart_log, "No docker_restart operation logged"


class TestPullCommandLogging:
    """Test logging in pull() command."""

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_pull_logs_with_operation(self, mock_run, docker_compose_wrapper):
        """Verify pull() logs with operation field."""
        mock_run.return_value = MagicMock(exit_code=0)

        docker_compose_wrapper.pull()

        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        found_pull_log = False
        for call_obj in debug_calls:
            if "docker_pull" in str(call_obj):
                found_pull_log = True
                args, kwargs = call_obj
                if "extra" in kwargs:
                    extra = kwargs["extra"]
                    assert extra.get("operation") == "docker_pull"

        assert found_pull_log, "No docker_pull operation logged"


class TestExecCommandLogging:
    """Test logging in exec() command."""

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_exec_logs_with_service_and_command(self, mock_run, docker_compose_wrapper):
        """Verify exec() logs with service and command."""
        mock_run.return_value = MagicMock(exit_code=0)

        docker_compose_wrapper.exec(service="frappe", command="bench version")

        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        found_exec_log = False
        for call_obj in debug_calls:
            if "docker_exec" in str(call_obj):
                found_exec_log = True
                args, kwargs = call_obj
                if "extra" in kwargs:
                    extra = kwargs["extra"]
                    assert extra.get("operation") == "docker_exec"
                    assert extra.get("service") == "frappe"

        assert found_exec_log, "No docker_exec operation logged"


class TestPsCommandLogging:
    """Test logging in ps() command."""

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_ps_logs_with_operation(self, mock_run, docker_compose_wrapper):
        """Verify ps() logs with operation field."""
        mock_run.return_value = MagicMock(exit_code=0, stdout=[b"CONTAINER ID  IMAGE"])

        docker_compose_wrapper.ps()

        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        found_ps_log = False
        for call_obj in debug_calls:
            if "docker_ps" in str(call_obj):
                found_ps_log = True
                args, kwargs = call_obj
                if "extra" in kwargs:
                    extra = kwargs["extra"]
                    assert extra.get("operation") == "docker_ps"

        assert found_ps_log, "No docker_ps operation logged"


class TestLogsCommandLogging:
    """Test logging in logs() command."""

    @patch("frappe_manager.docker.docker_compose.run_command_with_exit_code")
    def test_logs_logs_with_service_and_follow(self, mock_run, docker_compose_wrapper):
        """Verify logs() logs with service and follow flag."""
        mock_run.return_value = MagicMock(exit_code=0)

        docker_compose_wrapper.logs(services=["frappe"], follow=True)

        debug_calls = docker_compose_wrapper.logger.debug.call_args_list
        found_logs_log = False
        for call_obj in debug_calls:
            if "docker_logs" in str(call_obj):
                found_logs_log = True
                args, kwargs = call_obj
                if "extra" in kwargs:
                    extra = kwargs["extra"]
                    assert extra.get("operation") == "docker_logs"
                    assert extra.get("service") == "frappe" or extra.get("services") == ["frappe"]
                    assert extra.get("follow") is True

        assert found_logs_log, "No docker_logs operation logged"
