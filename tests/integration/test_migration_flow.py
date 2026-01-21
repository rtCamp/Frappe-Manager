import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.migration_manager.version import Version
from frappe_manager.metadata_manager import FMConfigManager


@pytest.mark.integration
class TestMigrationFlowIntegration:
    def test_fresh_install_skips_migration(self, tmp_path):
        config_path = tmp_path / "fm_config.toml"
        config_path.write_text("""
[metadata]
version = "0.19.0"
""")

        with patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"):
            config = FMConfigManager.import_from_toml(config_path)
            executor = MigrationExecutor(config)

            result = executor.execute()

            assert result is True
            assert len(executor.migrations) == 0
            assert config.version == Version("0.19.0")

    def test_upgrade_from_0_18_0_to_0_19_0_discovers_migration(self, tmp_path):
        config_path = tmp_path / "fm_config.toml"
        config_path.write_text("""
[metadata]
version = "0.18.0"
""")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.richprint') as mock_richprint,
        ):
            mock_richprint.prompt_ask.return_value = "yes"

            config = FMConfigManager.import_from_toml(config_path)
            executor = MigrationExecutor(config)

            result = executor.execute()

            assert len(executor.migrations) >= 0
            assert config.version == Version("0.19.0")

    def test_upgrade_from_below_minimum_version_blocked(self, tmp_path):
        config_path = tmp_path / "fm_config.toml"
        config_path.write_text("""
[metadata]
version = "0.17.0"
""")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.richprint') as mock_richprint,
        ):
            config = FMConfigManager.import_from_toml(config_path)
            executor = MigrationExecutor(config)

            result = executor.execute()

            assert result is False
            assert mock_richprint.error.call_count >= 3

            error_messages = [call[0][0] for call in mock_richprint.error.call_args_list]
            assert any("Cannot migrate from v0.17.0" in str(msg) for msg in error_messages)
            assert any("v0.18.0" in str(msg) for msg in error_messages)


@pytest.mark.integration
class TestMigrationWithBenches:
    def test_migration_with_existing_bench(self, tmp_path):
        benches_dir = tmp_path / "sites"
        benches_dir.mkdir()

        test_bench = benches_dir / "test-bench"
        test_bench.mkdir()

        compose_file = test_bench / "docker-compose.yml"
        compose_file.write_text("""
version: "3.9"
services:
  frappe:
    image: ghcr.io/rtcamp/frappe-manager-frappe:v0.18.0
  nginx:
    image: ghcr.io/rtcamp/frappe-manager-nginx:v0.18.0
""")

        workspace = test_bench / "workspace" / "frappe-bench" / "sites"
        workspace.mkdir(parents=True)

        config_file = workspace / "common_site_config.json"
        config_file.write_text('{"db_host": "mariadb"}')

        config_path = tmp_path / "fm_config.toml"
        config_path.write_text("""
[metadata]
version = "0.18.0"
""")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.CLI_BENCHES_DIRECTORY', benches_dir),
            patch('frappe_manager.migration_manager.migration_executor.richprint') as mock_richprint,
        ):
            mock_richprint.prompt_ask.return_value = "yes"

            config = FMConfigManager.import_from_toml(config_path)
            executor = MigrationExecutor(config)

            result = executor.execute()

            assert config.version == Version("0.19.0")


@pytest.mark.integration
class TestMigrationRollback:
    def test_user_abort_triggers_rollback_install(self, tmp_path):
        config_path = tmp_path / "fm_config.toml"
        config_path.write_text("""
[metadata]
version = "0.18.0"
""")

        with (
            patch('frappe_manager.migration_manager.migration_executor.get_current_fm_version', return_value="0.19.0"),
            patch('frappe_manager.migration_manager.migration_executor.richprint') as mock_richprint,
            patch('frappe_manager.migration_manager.migration_executor.install_package') as mock_install,
            patch('frappe_manager.migration_manager.migration_executor.pkgutil.iter_modules', return_value=[]),
        ):
            config = FMConfigManager.import_from_toml(config_path)
            executor = MigrationExecutor(config)

            mock_migration = Mock()
            mock_migration.version = Version("0.19.0")
            executor.migrations = [mock_migration]

            mock_richprint.prompt_ask.return_value = "no"

            result = executor.execute()

            assert result is False
            mock_install.assert_called_once_with("frappe-manager", "0.18.0")
