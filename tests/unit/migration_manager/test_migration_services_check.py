import pytest
from unittest.mock import Mock, MagicMock, patch, call
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.migration_manager.version import Version


class TestMigrationServicesAutoStart:
    def test_auto_starts_services_when_not_running(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")

        mock_services_manager = MagicMock()
        mock_services_manager.path.exists.return_value = True
        mock_services_manager.compose_file_manager.get_services_list.return_value = ["global-db", "global-nginx-proxy"]
        mock_services_manager.is_service_running.return_value = False

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.18.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.services_manager.services.ServicesManager', return_value=mock_services_manager),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, output_handler=mock_output)
            executor._ensure_global_services_running()

            mock_services_manager.init.assert_called_once()
            mock_services_manager.start_service.assert_called_once()
            
            print_calls = [call[0][0] for call in mock_output.print.call_args_list]
            assert any("Global services not running" in str(call) for call in print_calls)
            assert any("started successfully" in str(call) for call in print_calls)

    def test_skips_start_when_services_already_running(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")

        mock_services_manager = MagicMock()
        mock_services_manager.path.exists.return_value = True
        mock_services_manager.compose_file_manager.get_services_list.return_value = ["global-db", "global-nginx-proxy"]
        mock_services_manager.is_service_running.return_value = True

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.18.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.services_manager.services.ServicesManager', return_value=mock_services_manager),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, output_handler=mock_output)
            executor._ensure_global_services_running()

            mock_services_manager.init.assert_called_once()
            mock_services_manager.start_service.assert_not_called()
            
            mock_output.print.assert_not_called()

    def test_creates_services_when_not_initialized(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")

        mock_services_manager = MagicMock()
        mock_services_manager.path.exists.return_value = False

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.18.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.services_manager.services.ServicesManager', return_value=mock_services_manager),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, output_handler=mock_output)
            executor._ensure_global_services_running()

            mock_services_manager.init.assert_called_once()
            mock_services_manager.entrypoint_checks.assert_called_once_with(start=True)
            
            print_calls = [call[0][0] for call in mock_output.print.call_args_list]
            assert any("not initialized" in str(call) for call in print_calls)
            assert any("Creating" in str(call) for call in print_calls)

    def test_handles_service_start_failure_gracefully(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")

        mock_services_manager = MagicMock()
        mock_services_manager.path.exists.return_value = True
        mock_services_manager.init.side_effect = Exception("Docker daemon not running")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.18.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger') as mock_logger,
            patch('frappe_manager.services_manager.services.ServicesManager', return_value=mock_services_manager),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, output_handler=mock_output)
            executor._ensure_global_services_running()

            mock_logger.return_value.error.assert_called_once()
            error_call = mock_logger.return_value.error.call_args[0][0]
            assert "Failed to ensure global services" in error_call
            
            print_calls = [call[0][0] for call in mock_output.print.call_args_list]
            assert any("Warning" in str(call) for call in print_calls)
            assert any("fm services start" in str(call) for call in print_calls)

    def test_services_check_called_before_migrations_run(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        mock_fm_config.export_to_toml = Mock(return_value=True)

        mock_services_manager = MagicMock()
        mock_services_manager.path.exists.return_value = True
        mock_services_manager.compose_file_manager.get_services_list.return_value = ["global-db"]
        mock_services_manager.is_service_running.return_value = True

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.services_manager.services.ServicesManager', return_value=mock_services_manager),
            patch('frappe_manager.migration_manager.migration_executor.pkgutil.iter_modules', return_value=[]),
        ):
            mock_output = Mock()
            executor = MigrationExecutor(mock_fm_config, force=True, migrate_system=True, output_handler=mock_output)
            executor.execute()

            mock_services_manager.init.assert_called()
