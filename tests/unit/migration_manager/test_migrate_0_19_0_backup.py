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

        with patch(
            "frappe_manager.services_manager.database_service_manager.DatabaseServerServiceInfo.import_from_bench",
            return_value=None,
        ):
            with patch.object(migration, "bench_db_backup", return_value=None):
                migration.bench_basic_backup(mock_bench)

        backup_found = any(backup.src.name == "bench_config.toml" for backup in migration.backup_manager.backups)
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

        with patch(
            "frappe_manager.services_manager.database_service_manager.DatabaseServerServiceInfo.import_from_bench",
            return_value=None,
        ):
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
        """Verify _backup_env_for_rollback moves env/ to env.backup.migration."""
        env_dir = mock_bench.path / "workspace" / "frappe-bench" / "env"
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "bin").mkdir(parents=True, exist_ok=True)
        (env_dir / "bin" / "python3.10").write_text("#!/usr/bin/env python3.10")

        migration._backup_env_for_rollback(mock_bench)  # noqa: SLF001

        env_backup_path = mock_bench.path / "workspace" / "frappe-bench" / "env.backup.migration"
        assert env_backup_path.exists(), "env.backup.migration should exist"
        assert not env_dir.exists(), "Original env/ should be moved (not exist)"
        assert (env_backup_path / "bin" / "python3.10").exists(), "Backup should contain env contents"

    def test_env_rollback_removes_new_env_first(self, migration, mock_bench, tmp_path):
        """Verify undo_bench_migrate moves env.backup.migration back to env/."""
        env_dir = mock_bench.path / "workspace" / "frappe-bench" / "env"
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "old_marker.txt").write_text("old environment")

        migration._backup_env_for_rollback(mock_bench)  # noqa: SLF001

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

    def test_env_backup_skipped_when_env_path_is_a_file(self, migration, mock_bench, tmp_path):
        """env/ must be moved aside ONLY when it is a real directory.

        A stray non-directory at the env/ path is not a virtualenv, so the rollback
        backup must refuse it and leave it exactly where it is. Moving it would both
        destroy the file's location and plant a bogus env.backup.migration that
        undo_bench_migrate would later restore as env/.
        """
        frappe_bench = mock_bench.path / "workspace" / "frappe-bench"
        frappe_bench.mkdir(parents=True, exist_ok=True)
        env_path = frappe_bench / "env"
        env_path.write_text("not a virtualenv")
        migration.output = Mock()

        migration._backup_env_for_rollback(mock_bench)  # noqa: SLF001

        env_backup_path = frappe_bench / "env.backup.migration"
        assert env_path.is_file(), "A non-directory env/ must be left untouched"
        assert env_path.read_text() == "not a virtualenv"
        assert not env_backup_path.exists(), "No env backup may be created from a non-directory"
        migration.output.print.assert_not_called()

    def test_env_backup_skipped_when_env_missing(self, migration, mock_bench, tmp_path):
        """No env/ at all => nothing to back up, no backup directory created."""
        frappe_bench = mock_bench.path / "workspace" / "frappe-bench"
        frappe_bench.mkdir(parents=True, exist_ok=True)
        migration.output = Mock()

        migration._backup_env_for_rollback(mock_bench)  # noqa: SLF001

        assert not (frappe_bench / "env.backup.migration").exists()
        migration.output.print.assert_not_called()

    def test_env_backup_replaces_stale_backup_from_prior_attempt(self, migration, mock_bench, tmp_path):
        """A leftover env.backup.migration is replaced, not merged."""
        frappe_bench = mock_bench.path / "workspace" / "frappe-bench"
        env_dir = frappe_bench / "env"
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "current.txt").write_text("current")

        stale_backup = frappe_bench / "env.backup.migration"
        stale_backup.mkdir(parents=True, exist_ok=True)
        (stale_backup / "stale.txt").write_text("stale")

        migration._backup_env_for_rollback(mock_bench)  # noqa: SLF001

        assert (stale_backup / "current.txt").exists(), "Backup must hold the current env contents"
        assert not (stale_backup / "stale.txt").exists(), "Stale backup contents must be discarded"
        assert not env_dir.exists()


class TestBackupManagerNewFileTracking:
    """Tests for BackupManager.track_new_file and cleanup_new_files."""

    @pytest.fixture
    def backup_manager(self, tmp_path):
        return BackupManager(
            name="test-migration",
            benches_dir=tmp_path / "sites",
        )

    def test_track_new_file_tracks_existing_file(self, backup_manager, tmp_path):
        """Track a file that exists — should be added to new_files list."""
        test_file = tmp_path / "new-file.txt"
        test_file.write_text("test content")

        backup_manager.track_new_file(test_file)

        assert test_file in backup_manager.new_files
        assert len(backup_manager.new_files) == 1

    def test_track_new_file_ignores_nonexistent_file(self, backup_manager, tmp_path):
        """Track a file that doesn't exist — should not be added."""
        test_file = tmp_path / "does-not-exist.txt"

        backup_manager.track_new_file(test_file)

        assert len(backup_manager.new_files) == 0

    def test_cleanup_new_files_deletes_tracked_files(self, backup_manager, tmp_path):
        """cleanup_new_files should delete files that were tracked."""
        test_file = tmp_path / "to-delete.txt"
        test_file.write_text("test content")
        backup_manager.track_new_file(test_file)

        assert test_file.exists()

        backup_manager.cleanup_new_files()

        assert not test_file.exists()
        assert len(backup_manager.new_files) == 0

    def test_cleanup_new_files_deletes_tracked_dirs(self, backup_manager, tmp_path):
        """cleanup_new_files should delete directories that were tracked."""
        test_dir = tmp_path / "to-delete-dir"
        test_dir.mkdir()
        (test_dir / "nested-file.txt").write_text("nested")
        backup_manager.track_new_file(test_dir)

        assert test_dir.exists()

        backup_manager.cleanup_new_files()

        assert not test_dir.exists()

    def test_cleanup_new_files_preserves_pre_existing_files(self, backup_manager, tmp_path):
        """Files that existed pre-migration (never tracked) must NOT be deleted.

        This is the key safety guarantee: cleanup_new_files must only affect
        files that were explicitly tracked with track_new_file().
        """
        pre_existing = tmp_path / "pre-existing.txt"
        pre_existing.write_text("original data")

        new_file = tmp_path / "new-file.txt"
        new_file.write_text("new data")
        backup_manager.track_new_file(new_file)

        backup_manager.cleanup_new_files()

        # Pre-existing file must survive
        assert pre_existing.exists(), "Pre-existing file should not be deleted"
        assert pre_existing.read_text() == "original data"

        # Only the tracked new file should be gone
        assert not new_file.exists(), "Tracked new file should be deleted"

    def test_cleanup_new_files_idempotent(self, backup_manager, tmp_path):
        """Calling cleanup_new_files twice should be safe (no errors, no leftover files)."""
        test_file = tmp_path / "to-delete.txt"
        test_file.write_text("test content")
        backup_manager.track_new_file(test_file)

        # First cleanup
        backup_manager.cleanup_new_files()
        assert not test_file.exists()
        assert len(backup_manager.new_files) == 0

        # Second cleanup — must not raise
        backup_manager.cleanup_new_files()

    def test_track_new_file_handles_multiple_files(self, backup_manager, tmp_path):
        """Multiple files can be tracked and cleaned up together."""
        files = []
        for i in range(3):
            f = tmp_path / f"new-file-{i}.txt"
            f.write_text(f"content-{i}")
            backup_manager.track_new_file(f)
            files.append(f)

        assert len(backup_manager.new_files) == 3

        backup_manager.cleanup_new_files()

        for f in files:
            assert not f.exists(), f"Tracked file {f.name} should be deleted"
