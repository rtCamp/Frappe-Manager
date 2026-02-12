"""
Tests for v0.19.0 migration backup functionality.

Verifies that bench_config.toml, supervisor configs, and env/ directory
are backed up and restored correctly during migration rollback.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.migration_manager.migration_helpers import MigrationBench
from frappe_manager.migration_manager.migrations.migrate_0_19_0 import MigrationV0190


class TestMigrationV0190RuntimeBackup:
    """Test runtime-related backup during migration (env, supervisor, bench_config)."""

    @pytest.fixture
    def migration(self, tmp_path):
        """Create migration instance with temporary directories."""
        migration = MigrationV0190()
        migration.benches_dir = tmp_path / "sites"
        migration.benches_dir.mkdir(parents=True, exist_ok=True)

        # Mock migration_executor required after refactoring
        mock_executor = Mock()
        mock_executor.skip_backup = False
        mock_executor.skip_backup_for = []
        migration.migration_executor = mock_executor

        return migration

    @pytest.fixture
    def mock_bench(self, tmp_path):
        bench_path = tmp_path / "sites" / "test-bench"
        bench_path.mkdir(parents=True, exist_ok=True)

        bench_config = bench_path / "bench_config.toml"
        bench_config.write_text("""
[supervisor]
user = "frappe"

[server]
host = "0.0.0.0"
port = 8000
""")

        compose_file = bench_path / "docker-compose.yml"
        compose_file.write_text("version: '3.7'\nservices:\n  frappe:\n    image: test")

        with (
            patch("frappe_manager.migration_manager.migration_helpers.DockerClient"),
            patch("frappe_manager.migration_manager.migration_helpers.ComposeFile") as mock_compose_file,
        ):
            mock_compose_file_instance = MagicMock()
            mock_compose_file_instance.compose_path = compose_file
            mock_compose_file.return_value = mock_compose_file_instance

            bench = MigrationBench(name="test-bench", path=bench_path)

        return bench

    def test_bench_basic_backup_includes_bench_config(self, migration, mock_bench, tmp_path):
        """Verify that bench_basic_backup() backs up bench_config.toml via parent."""
        migration.backup_manager = BackupManager(
            name=str(migration.version),
            benches_dir=migration.benches_dir,
        )

        with patch("frappe_manager.services_manager.database_service_manager.DatabaseServerServiceInfo.import_from_bench", return_value=None):
            with patch.object(migration, "bench_db_backup", return_value=None):
                migration.bench_basic_backup(mock_bench)

        backup_found = any(
            backup.src.name == "bench_config.toml"
            for backup in migration.backup_manager.backups
        )
        assert backup_found, "bench_config.toml should be in backup list"

    def test_bench_config_backup_creates_file(self, migration, mock_bench, tmp_path):
        """Verify that backup actually creates a backup file."""
        migration.backup_manager = BackupManager(
            name=str(migration.version),
            benches_dir=migration.benches_dir,
        )

        bench_config_path = mock_bench.path / "bench_config.toml"
        original_content = bench_config_path.read_text()

        backup_data = migration.backup_manager.backup(
            bench_config_path,
            bench_name=mock_bench.name,
        )

        assert backup_data is not None, "Backup should return BackupData"
        assert backup_data.real_dest.exists(), "Backup file should exist"
        assert backup_data.real_dest.read_text() == original_content, "Backup content should match original"

    def test_bench_config_restore_works(self, migration, mock_bench, tmp_path):
        """Verify that bench_config.toml can be restored from backup."""
        migration.backup_manager = BackupManager(
            name=str(migration.version),
            benches_dir=migration.benches_dir,
        )

        bench_config_path = mock_bench.path / "bench_config.toml"
        original_content = bench_config_path.read_text()

        backup_data = migration.backup_manager.backup(
            bench_config_path,
            bench_name=mock_bench.name,
        )

        modified_content = original_content + '\npython_version = "3.11"\nnode_version = "18"\n'
        bench_config_path.write_text(modified_content)

        assert bench_config_path.read_text() != original_content, "File should be modified"

        migration.backup_manager.restore(backup_data, force=True)

        assert bench_config_path.read_text() == original_content, "File should be restored to original content"

    def test_bench_config_backup_skipped_if_not_exists(self, migration, mock_bench, tmp_path):
        """Verify that backup is skipped gracefully if bench_config.toml doesn't exist."""
        migration.backup_manager = BackupManager(
            name=str(migration.version),
            benches_dir=migration.benches_dir,
        )

        bench_config_path = mock_bench.path / "bench_config.toml"
        bench_config_path.unlink()

        backup_data = migration.backup_manager.backup(
            bench_config_path,
            bench_name=mock_bench.name,
        )

        assert backup_data is None, "Backup should return None for non-existent files"

    def test_integration_bench_config_survives_rollback(self, migration, mock_bench, tmp_path):
        """Full rollback test: backup → modify → rollback → verify restoration."""
        migration.backup_manager = BackupManager(
            name=str(migration.version),
            benches_dir=migration.benches_dir,
        )

        bench_config_path = mock_bench.path / "bench_config.toml"
        original_content = bench_config_path.read_text()

        with patch("frappe_manager.services_manager.database_service_manager.DatabaseServerServiceInfo.import_from_bench", return_value=None):
            with patch.object(migration, "bench_db_backup", return_value=None):
                migration.bench_basic_backup(mock_bench)

        import tomlkit
        doc = tomlkit.parse(bench_config_path.read_text())
        doc["python_version"] = "3.11"
        doc["node_version"] = "18"
        bench_config_path.write_text(tomlkit.dumps(doc))

        modified_content = bench_config_path.read_text()
        assert "python_version" in modified_content, "File should be modified"
        assert "python_version" not in original_content, "Original should not have python_version"

        for backup in migration.backup_manager.backups:
            if backup.src.name == "bench_config.toml":
                migration.backup_manager.restore(backup, force=True)

        restored_content = bench_config_path.read_text()
        assert restored_content == original_content, "bench_config.toml should be restored to original"
        assert "python_version" not in restored_content, "Restored file should not have migration changes"

    def test_supervisor_configs_backed_up(self, migration, mock_bench, tmp_path):
        """Verify supervisor configs are backed up."""
        migration.backup_manager = BackupManager(
            name=str(migration.version),
            benches_dir=migration.benches_dir,
        )

        config_dir = mock_bench.path / "workspace" / "frappe-bench" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        (config_dir / "supervisor.conf").write_text("[supervisord]\nlogfile=/tmp/supervisord.log\n")
        (config_dir / "web.fm.supervisor.conf").write_text("[program:frappe-web]\ncommand=bench serve\n")
        (config_dir / "socketio.fm.supervisor.conf").write_text("[program:frappe-socketio]\ncommand=node\n")

        with patch.object(MigrationV0190.__bases__[0], "bench_basic_backup", return_value=None):
            migration.bench_basic_backup(mock_bench)

        backed_up_files = [backup.src.name for backup in migration.backup_manager.backups]

        assert "supervisor.conf" in backed_up_files
        assert "web.fm.supervisor.conf" in backed_up_files
        assert "socketio.fm.supervisor.conf" in backed_up_files

    def test_env_directory_backed_up(self, migration, mock_bench, tmp_path):
        """Verify env/ directory is moved to env.backup.migration."""
        migration.backup_manager = BackupManager(
            name=str(migration.version),
            benches_dir=migration.benches_dir,
        )

        env_dir = mock_bench.path / "workspace" / "frappe-bench" / "env"
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "bin").mkdir(parents=True, exist_ok=True)
        (env_dir / "bin" / "python3.10").write_text("#!/usr/bin/env python3.10")

        with patch.object(MigrationV0190.__bases__[0], "bench_basic_backup", return_value=None):
            migration.bench_basic_backup(mock_bench)

        env_backup_path = mock_bench.path / "workspace" / "frappe-bench" / "env.backup.migration"
        assert env_backup_path.exists(), "env.backup.migration should exist"
        assert not env_dir.exists(), "Original env/ should be moved (not exist)"
        assert (env_backup_path / "bin" / "python3.10").exists(), "Backup should contain env contents"

    def test_env_rollback_removes_new_env_first(self, migration, mock_bench, tmp_path):
        """Verify undo_bench_migrate moves env.backup.migration back to env/."""
        migration.backup_manager = BackupManager(
            name=str(migration.version),
            benches_dir=migration.benches_dir,
        )

        env_dir = mock_bench.path / "workspace" / "frappe-bench" / "env"
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "old_marker.txt").write_text("old environment")

        with patch.object(MigrationV0190.__bases__[0], "bench_basic_backup", return_value=None):
            migration.bench_basic_backup(mock_bench)

        env_backup_path = mock_bench.path / "workspace" / "frappe-bench" / "env.backup.migration"
        assert env_backup_path.exists()
        assert not env_dir.exists()

        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "new_marker.txt").write_text("new environment")

        migration.undo_bench_migrate(mock_bench)

        assert env_dir.exists(), "env/ should be restored"
        assert (env_dir / "old_marker.txt").exists(), "Old env should be restored"
        assert not (env_dir / "new_marker.txt").exists(), "New env should be removed"
        assert not env_backup_path.exists(), "Backup should be moved back (not exist)"
