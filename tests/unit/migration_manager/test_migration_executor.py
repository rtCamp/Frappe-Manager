import pytest
from unittest.mock import Mock, MagicMock, patch
from frappe_manager.migration_manager.migration_executor import MigrationExecutor, MINIMUM_SUPPORTED_VERSION
from frappe_manager.migration_manager.version import Version


class TestMigrationExecutorVersionEnforcement:
    def test_no_migration_when_versions_equal(self, mock_fm_config):
        mock_fm_config.version = Version("0.19.0")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            executor = MigrationExecutor(mock_fm_config)
            result = executor.execute()

            assert result is True
            assert len(executor.migrations) == 0

    def test_no_migration_when_current_version_lower(self, mock_fm_config):
        mock_fm_config.version = Version("0.19.0")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.18.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            executor = MigrationExecutor(mock_fm_config)
            result = executor.execute()

            assert result is True
            assert len(executor.migrations) == 0

    def test_blocks_migration_below_minimum_version(self, mock_fm_config):
        mock_fm_config.version = Version("0.17.0")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.migration_manager.migration_executor.richprint') as mock_richprint,
        ):
            executor = MigrationExecutor(mock_fm_config)
            result = executor.execute()

            assert result is False
            assert mock_richprint.error.call_count >= 3

            error_calls = [call[0][0] for call in mock_richprint.error.call_args_list]
            assert any("Cannot migrate from v0.17.0" in str(call) for call in error_calls)
            assert any("v0.18.0" in str(call) for call in error_calls)

    def test_allows_migration_from_minimum_version(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.migration_manager.migration_executor.pkgutil.iter_modules', return_value=[]),
            patch('frappe_manager.migration_manager.migration_executor.richprint') as mock_richprint,
        ):
            executor = MigrationExecutor(mock_fm_config)
            result = executor.execute()

            assert result is True

    def test_minimum_supported_version_is_0_18_0(self):
        assert MINIMUM_SUPPORTED_VERSION == Version("0.18.0")

    def test_migration_executor_sets_correct_versions(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            executor = MigrationExecutor(mock_fm_config)

            assert executor.prev_version == Version("0.18.0")
            assert executor.current_version == Version("0.19.0")
            assert executor.rollback_version == Version("0.18.0")


class TestMigrationExecutorMigrationDiscovery:
    def test_discovers_migration_in_version_range(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        mock_fm_config.export_to_toml = Mock(return_value=True)

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.migration_manager.migration_executor.richprint'),
        ):
            executor = MigrationExecutor(mock_fm_config)
            result = executor.execute()

            assert executor.prev_version == Version("0.18.0")
            assert executor.current_version == Version("0.19.0")
            assert result is True

    def test_skips_migration_outside_version_range(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")

        mock_migration_class = Mock()
        mock_migration_class.version = Version("0.20.0")
        mock_migration_class.up = Mock()
        mock_migration_class.down = Mock()
        mock_migration_class.set_migration_executor = Mock()
        mock_migration_instance = Mock()
        mock_migration_instance.version = Version("0.20.0")
        mock_migration_class.return_value = mock_migration_instance

        mock_module = Mock()
        setattr(mock_module, 'Migrate_0_20_0', mock_migration_class)

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch(
                'frappe_manager.migration_manager.migration_executor.pkgutil.iter_modules',
                return_value=[(None, 'migrate_0_20_0', None)],
            ),
            patch(
                'frappe_manager.migration_manager.migration_executor.importlib.import_module', return_value=mock_module
            ),
            patch('frappe_manager.migration_manager.migration_executor.richprint'),
        ):
            executor = MigrationExecutor(mock_fm_config)
            executor.execute()

            assert len(executor.migrations) == 0

    def test_skips_version_0_0_0_migrations(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")

        mock_migration_class = Mock()
        mock_migration_class.version = Version("0.0.0")
        mock_migration_class.up = Mock()
        mock_migration_class.down = Mock()
        mock_migration_class.set_migration_executor = Mock()

        mock_module = Mock()
        setattr(mock_module, 'Migrate_0_0_0', mock_migration_class)

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch(
                'frappe_manager.migration_manager.migration_executor.pkgutil.iter_modules',
                return_value=[(None, 'migrate_0_0_0', None)],
            ),
            patch(
                'frappe_manager.migration_manager.migration_executor.importlib.import_module', return_value=mock_module
            ),
            patch('frappe_manager.migration_manager.migration_executor.richprint'),
        ):
            executor = MigrationExecutor(mock_fm_config)
            executor.execute()

            assert len(executor.migrations) == 0


class TestMigrationExecutorUserPrompt:
    def test_prompts_user_when_migrations_pending(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")

        mock_migration = Mock()
        mock_migration.version = Version("0.19.0")
        mock_migration.up = Mock()
        mock_migration.get_rollback_version = Mock(return_value=Version("0.19.0"))

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.migration_manager.migration_executor.pkgutil.iter_modules', return_value=[]),
            patch('frappe_manager.migration_manager.migration_executor.richprint') as mock_richprint,
        ):
            executor = MigrationExecutor(mock_fm_config)
            executor.migrations = [mock_migration]

            mock_richprint.prompt_ask.return_value = "yes"

            executor.execute()

            assert mock_richprint.prompt_ask.called
            prompt_text = mock_richprint.prompt_ask.call_args[1]['prompt']
            assert "Do you want to proceed with the migration" in prompt_text

    def test_aborts_and_reverts_when_user_says_no(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")

        mock_migration = Mock()
        mock_migration.version = Version("0.19.0")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.migration_manager.migration_executor.pkgutil.iter_modules', return_value=[]),
            patch('frappe_manager.migration_manager.migration_executor.richprint') as mock_richprint,
            patch('frappe_manager.migration_manager.migration_executor.install_package') as mock_install,
        ):
            executor = MigrationExecutor(mock_fm_config)
            executor.migrations = [mock_migration]

            mock_richprint.prompt_ask.return_value = "no"

            result = executor.execute()

            assert result is False
            mock_install.assert_called_once_with("frappe-manager", "0.18.0")
