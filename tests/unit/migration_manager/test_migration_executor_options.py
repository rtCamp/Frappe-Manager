import pytest
from unittest.mock import Mock, MagicMock, patch
from frappe_manager.migration_manager.migration_executor import MigrationExecutor, needs_migration
from frappe_manager.migration_manager.version import Version


class TestNeedsMigration:
    def test_needs_migration_when_current_version_higher(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        
        with patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"):
            result = needs_migration(mock_fm_config)
            assert result is True

    def test_no_migration_when_versions_equal(self, mock_fm_config):
        mock_fm_config.version = Version("0.19.0")
        
        with patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"):
            result = needs_migration(mock_fm_config)
            assert result is False

    def test_no_migration_when_current_version_lower(self, mock_fm_config):
        mock_fm_config.version = Version("0.19.0")
        
        with patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.18.0"):
            result = needs_migration(mock_fm_config)
            assert result is False


class TestMigrationExecutorWithOptions:
    def test_executor_accepts_skip_backup_option(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        
        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            executor = MigrationExecutor(mock_fm_config, skip_backup=True)
            
            assert executor.skip_backup is True
            assert executor.skip_backup_for == []
            assert executor.exclude_benches == []
            assert executor.force is False

    def test_executor_accepts_skip_backup_for_list(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        
        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            executor = MigrationExecutor(
                mock_fm_config,
                skip_backup_for=["bench1", "bench2"]
            )
            
            assert executor.skip_backup is False
            assert executor.skip_backup_for == ["bench1", "bench2"]

    def test_executor_accepts_exclude_benches_list(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        
        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            executor = MigrationExecutor(
                mock_fm_config,
                exclude_benches=["old-bench", "test-bench"]
            )
            
            assert executor.exclude_benches == ["old-bench", "test-bench"]

    def test_executor_accepts_force_option(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        
        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            executor = MigrationExecutor(mock_fm_config, force=True)
            
            assert executor.force is True

    def test_executor_accepts_all_options_combined(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        
        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            executor = MigrationExecutor(
                mock_fm_config,
                skip_backup=False,
                skip_backup_for=["bench1"],
                exclude_benches=["old-bench"],
                force=True
            )
            
            assert executor.skip_backup is False
            assert executor.skip_backup_for == ["bench1"]
            assert executor.exclude_benches == ["old-bench"]
            assert executor.force is True


class TestMigrationExecutorForceFlag:
    def test_force_skips_initial_confirmation_prompt(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        mock_fm_config.export_to_toml = Mock(return_value=True)
        
        class MockMigration:
            version = Version("0.19.0")
            
            def up(self):
                pass
            
            def down(self):
                pass
            
            def set_migration_executor(self, migration_executor):
                self.migration_executor = migration_executor
            
            def get_rollback_version(self):
                return Version("0.19.0")
        
        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.migration_manager.migration_executor.richprint') as mock_richprint,
            patch('frappe_manager.migration_manager.migration_executor.pkgutil.iter_modules') as mock_iter,
            patch('frappe_manager.migration_manager.migration_executor.importlib.import_module') as mock_import,
        ):
            mock_iter.return_value = [(None, "migrate_0_19_0", False)]
            
            mock_module = Mock()
            setattr(mock_module, 'MigrationV0190', MockMigration)
            mock_import.return_value = mock_module
            
            executor = MigrationExecutor(mock_fm_config, force=True)
            result = executor.execute()
            
            mock_richprint.prompt_ask.assert_not_called()
            
            force_call_found = False
            for call in mock_richprint.print.call_args_list:
                if "Proceeding with migration (--force)" in str(call):
                    force_call_found = True
                    break
            
            assert force_call_found is True

    def test_without_force_shows_confirmation_prompt(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        mock_fm_config.export_to_toml = Mock(return_value=True)
        
        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
            patch('frappe_manager.migration_manager.migration_executor.richprint') as mock_richprint,
            patch('frappe_manager.migration_manager.migration_executor.pkgutil.iter_modules') as mock_iter,
        ):
            mock_iter.return_value = []
            mock_richprint.prompt_ask.return_value = "yes"
            
            executor = MigrationExecutor(mock_fm_config, force=False)
            result = executor.execute()
            
            assert result is True


class TestMigrationExecutorBackupOptions:
    def test_skip_backup_passed_to_migration_instance(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        
        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            executor = MigrationExecutor(mock_fm_config, skip_backup=True)
            
            assert executor.skip_backup is True

    def test_skip_backup_for_list_available_to_migration(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        
        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            skip_list = ["bench1", "bench2", "bench3"]
            executor = MigrationExecutor(mock_fm_config, skip_backup_for=skip_list)
            
            assert executor.skip_backup_for == skip_list
            assert "bench1" in executor.skip_backup_for
            assert "bench2" in executor.skip_backup_for


class TestMigrationExecutorExcludeBenches:
    def test_exclude_benches_list_available_to_migration(self, mock_fm_config):
        mock_fm_config.version = Version("0.18.0")
        
        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.log.get_logger'),
        ):
            exclude_list = ["old-bench", "broken-bench"]
            executor = MigrationExecutor(mock_fm_config, exclude_benches=exclude_list)
            
            assert executor.exclude_benches == exclude_list
            assert "old-bench" in executor.exclude_benches
            assert "broken-bench" in executor.exclude_benches


@pytest.fixture
def mock_fm_config():
    config = Mock()
    config.version = Version("0.18.0")
    config.export_to_toml = Mock(return_value=True)
    return config
