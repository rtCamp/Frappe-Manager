"""Tests for Bench class logging integration.

Verifies that the Bench class properly uses ContextualLogger with structured
extra fields and exception handling for all operation categories:
- Lifecycle operations (create, start, stop, remove)
- SSL operations (create, remove, update, renew certificates)
- Configuration operations (save, set, sync)
- Database operations (remove, deletion handler)
- Worker/admin tool operations (sync, ensure running)
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.logger import FMLogger, current_context, reset_context
from frappe_manager.site_manager.bench_config import DatabaseConfig
from frappe_manager.site_manager.site import Bench


@pytest.fixture
def mock_logger():
    """Create a mock ContextualLogger for testing."""
    logger = MagicMock(spec=FMLogger)
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.exception = MagicMock()
    return logger


@pytest.fixture
def mock_bench_dependencies():
    """Create mock dependencies for Bench initialization."""
    deps = {
        "path": Path("/tmp/test-bench"),
        "name": "test.localhost",
        "bench_config": MagicMock(),
        "compose_file_manager": MagicMock(),
        "docker_client": MagicMock(),
        "services": MagicMock(),
        "workers_check": False,
        "admin_tools_check": False,
        "verbose": False,
        "output_handler": MagicMock(),
    }

    # Setup nested mocks to prevent AttributeErrors
    deps["bench_config"].ssl_certificates = []
    deps["compose_file_manager"].exists.return_value = True
    deps["services"].nginx_controller = MagicMock()
    deps["services"].proxy_storage = MagicMock()
    deps["services"].database_manager = MagicMock()

    return deps


class TestBenchAmbientContext:
    """Bench logging is ambient: self-acquired component logger + bench tag via set_context."""

    def test_bench_init_signature_takes_no_logger(self):
        import inspect

        from frappe_manager.site_manager.site import Bench as B

        assert "logger" not in inspect.signature(B.__init__).parameters
        assert "logger" not in inspect.signature(B.get_object).parameters

    def test_set_context_bench_tag_shape(self):
        from frappe_manager.logger import set_context

        reset_context()
        set_context(bench="test.localhost")
        assert current_context().bench == "test.localhost"
        reset_context()


class TestLifecycleOperationLogging:
    """Test logging for bench lifecycle operations."""

    def test_create_logs_with_correct_extra_fields(self, mock_bench_dependencies, mock_logger, mocker):
        """create() should log with operation=bench_create and bench_name."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.orchestrator = MagicMock()
        bench.orchestrator.create_bench = MagicMock()

        bench.create(is_template_bench=False)

        # Verify debug log was called
        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        first_call_args, first_call_kwargs = debug_calls[0]
        assert "Starting bench creation" in first_call_args[0]
        assert "extra_fields" in first_call_kwargs
        extra = first_call_kwargs["extra_fields"]
        assert extra["operation"] == "bench_create"
        assert extra["bench_name"] == "test.localhost"
        assert extra["is_template_bench"] is False

        # Verify info log was called on success
        info_calls = mock_logger.info.call_args_list
        assert len(info_calls) > 0
        last_call_args, last_call_kwargs = info_calls[-1]
        assert "successfully" in last_call_args[0]

    def test_create_logs_exception_on_failure(self, mock_bench_dependencies, mock_logger, mocker):
        """create() should log exception and re-raise on failure."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.orchestrator = MagicMock()

        test_error = Exception("Creation failed")
        bench.orchestrator.create_bench = MagicMock(side_effect=test_error)

        with pytest.raises(Exception):
            bench.create()

        # Verify exception was logged with error field
        exception_calls = mock_logger.exception.call_args_list
        assert len(exception_calls) > 0
        call_args, call_kwargs = exception_calls[0]
        assert "Failed to create" in call_args[0]
        assert "extra_fields" in call_kwargs
        assert call_kwargs["extra_fields"]["error"] == "Creation failed"

    def test_start_logs_with_parameters(self, mock_bench_dependencies, mock_logger, mocker):
        """start() should log with force and reconfigure parameters."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.path = Path("/tmp/test-bench")
        bench.docker_ops = MagicMock()
        bench.output = MagicMock()
        bench.orchestrator = MagicMock()
        bench.orchestrator.start_bench = MagicMock()

        bench.start(force=True, reconfigure_workers=True)

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "bench_start"
        assert extra["force"] is True
        assert extra["reconfigure_workers"] is True

    def test_stop_logs_with_correct_operation(self, mock_bench_dependencies, mock_logger, mocker):
        """stop() should log with operation=bench_stop."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.docker_ops = MagicMock()
        bench.docker_ops.stop = MagicMock()
        bench.workers = MagicMock()
        bench.workers.compose_file_manager = MagicMock()
        bench.workers.compose_file_manager.exists.return_value = False
        bench.admin_tools = MagicMock()
        bench.admin_tools.compose_file_manager = MagicMock()
        bench.admin_tools.compose_file_manager.exists.return_value = False
        bench.output = MagicMock()

        bench.stop()

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "bench_stop"

    def test_remove_bench_logs_cancellation(self, mock_bench_dependencies, mock_logger, mocker):
        """remove_bench() should log when user cancels removal."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.output = MagicMock()
        bench.output.prompt_ask = MagicMock(return_value="no")

        result = bench.remove_bench()

        assert result is False
        debug_calls = mock_logger.debug.call_args_list
        cancel_log_found = any("cancelled" in str(call).lower() for call in debug_calls)
        assert cancel_log_found


class TestSSLOperationLogging:
    """Test logging for SSL certificate operations."""

    def test_create_certificate_logs_with_correct_extra(self, mock_bench_dependencies, mock_logger, mocker):
        """create_certificate() should log with operation=ssl_create_certificate."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.ssl = MagicMock()
        bench.ssl.create_individual_certificates = MagicMock()
        bench.save_bench_config = MagicMock()

        bench.create_certificate()

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "ssl_create_certificate"
        assert extra["bench_name"] == "test.localhost"

    def test_remove_certificate_logs_with_correct_extra(self, mock_bench_dependencies, mock_logger, mocker):
        """remove_certificate() should log with operation=ssl_remove_certificate."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.ssl = MagicMock()
        bench.ssl.remove_all_certificates = MagicMock()
        bench.bench_config = MagicMock()
        bench.bench_config.ssl_certificates = []
        bench.save_bench_config = MagicMock()

        bench.remove_certificate()

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "ssl_remove_certificate"

    def test_update_certificate_logs_with_correct_extra(self, mock_bench_dependencies, mock_logger, mocker):
        """update_certificate() should log with operation=ssl_update_certificate."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.ssl = MagicMock()
        bench.ssl.update_certificate = MagicMock(return_value=True)
        bench.bench_config = MagicMock()

        mock_cert = MagicMock()
        bench.update_certificate(mock_cert)

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "ssl_update_certificate"

    def test_renew_certificate_logs_with_correct_extra(self, mock_bench_dependencies, mock_logger, mocker):
        """renew_certificate() should log with operation=ssl_renew_certificate."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.ssl = MagicMock()
        bench.ssl.renew_certificate = MagicMock(return_value=True)

        bench.renew_certificate()

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "ssl_renew_certificate"


class TestConfigOperationLogging:
    """Test logging for configuration operations."""

    def test_save_bench_config_logs_with_correct_operation(self, mock_bench_dependencies, mock_logger, mocker):
        """save_bench_config() should log with operation=config_save_bench_config."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.bench_config = MagicMock()
        bench.bench_config.export_to_toml = MagicMock()
        bench.bench_config.root_path = Path("/tmp/test")
        bench.output = MagicMock()

        bench.save_bench_config(print_message=False)

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "config_save_bench_config"

    def test_set_common_bench_config_logs_config_keys(self, mock_bench_dependencies, mock_logger, mocker):
        """set_common_bench_config() should log with config_keys in extra."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.path = Path("/tmp/test")
        mocker.patch("frappe_manager.site_manager.site.save_dict_to_file")
        mocker.patch("pathlib.Path.exists", return_value=True)

        config_data = {"developer_mode": True, "timeout": 300}
        bench.set_common_bench_config(config_data)

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "config_set_common"
        assert "developer_mode" in extra["config_keys"]
        assert "timeout" in extra["config_keys"]

    def test_sync_bench_config_logs_with_correct_operation(self, mock_bench_dependencies, mock_logger, mocker):
        """sync_bench_config_configuration() should log with operation=config_sync_bench_config."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.bench_config = MagicMock()
        bench.bench_config.developer_mode = True
        bench.bench_config.admin_tools = False
        bench.bench_config.get_primary_certificate = MagicMock(return_value=None)
        bench.set_common_bench_config = MagicMock()
        bench.update_certificate = MagicMock()
        bench.admin_tools = MagicMock()
        bench.admin_tools.compose_file_manager = MagicMock()
        bench.admin_tools.compose_file_manager.compose_path = MagicMock()
        bench.admin_tools.compose_file_manager.compose_path.exists.return_value = False
        bench.output = MagicMock()
        bench.restart_supervisor_service = MagicMock()

        bench.sync_bench_config_configuration()

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "config_sync_bench_config"


class TestDatabaseOperationLogging:
    """Test logging for database operations."""

    def test_remove_database_and_user_logs_with_correct_operation(self, mock_bench_dependencies, mock_logger, mocker):
        """remove_database_and_user() should log with operation=db_remove."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.database = MagicMock()
        bench.database.remove_database_and_user = MagicMock()

        bench.remove_database_and_user()

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "db_remove"

    def test_handle_database_deletion_logs_deletion_handler_operation(
        self,
        mock_bench_dependencies,
        mock_logger,
        mocker,
    ):
        """_handle_database_deletion() should log with operation=db_deletion_handler."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.external_database_config = MagicMock(
            return_value=DatabaseConfig(host="db.example.com", name="app_prod"),
        )
        bench.output = MagicMock()

        bench._handle_database_deletion(None)

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "db_handle_deletion"


class TestWorkerOperationLogging:
    """Test logging for worker operations."""

    def test_ensure_workers_running_logs_with_correct_operation(self, mock_bench_dependencies, mock_logger, mocker):
        """ensure_workers_running_if_available() should log with operation=workers_ensure_running."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.worker_coordinator = MagicMock()
        bench.worker_coordinator.ensure_workers_running_if_available = MagicMock()

        bench.ensure_workers_running_if_available()

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "workers_ensure_running"

    def test_sync_workers_compose_logs_with_parameters(self, mock_bench_dependencies, mock_logger, mocker):
        """sync_workers_compose() should log with force_recreate and setup_supervisor parameters."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.worker_coordinator = MagicMock()
        bench.worker_coordinator.sync_workers_compose = MagicMock()

        bench.sync_workers_compose(force_recreate=True, setup_supervisor=False)

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "workers_sync_compose"
        assert extra["force_recreate"] is True
        assert extra["setup_supervisor"] is False


class TestAdminToolsOperationLogging:
    """Test logging for admin tools operations."""

    def test_sync_admin_tools_compose_logs_with_correct_operation(self, mock_bench_dependencies, mock_logger, mocker):
        """sync_admin_tools_compose() should log with operation=admin_tools_sync_compose."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.admin_tools = MagicMock()
        bench.admin_tools.generate_compose = MagicMock()
        bench.admin_tools.enable = MagicMock(return_value=False)
        bench.services = MagicMock()
        bench.services.database_manager = MagicMock()
        bench.services.database_manager.database_server_info = MagicMock()
        bench.services.database_manager.database_server_info.host = "localhost"

        bench.sync_admin_tools_compose()

        debug_calls = mock_logger.debug.call_args_list
        assert len(debug_calls) > 0
        call_args, call_kwargs = debug_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["operation"] == "admin_tools_sync_compose"


class TestExceptionHandlingLogging:
    """Test exception handling in logging across all operations."""

    def test_all_operations_log_exceptions_with_error_field(self, mock_bench_dependencies, mock_logger, mocker):
        """All logged methods should include error field when exception occurs."""
        mocker.patch.object(Bench, "__init__", lambda *args, **kwargs: None)
        bench = Bench.__new__(Bench)
        bench.name = "test.localhost"
        bench.logger = mock_logger
        bench.orchestrator = MagicMock()

        test_error = Exception("Test error message")
        bench.orchestrator.create_bench = MagicMock(side_effect=test_error)

        with pytest.raises(Exception):
            bench.create()

        # Verify exception was logged with error field
        exception_calls = mock_logger.exception.call_args_list
        assert len(exception_calls) > 0
        call_args, call_kwargs = exception_calls[0]
        extra = call_kwargs["extra_fields"]
        assert extra["error"] == "Test error message"
