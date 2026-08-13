"""
Tests for BackupManager.restore() bookkeeping and skip conditions.

Contract defended here: ``BackupData.is_restored`` is the record of whether a restore
actually happened. Rollback code inspects it to decide whether a file has already been
put back, so the flag must be flipped to True by a restore that copies data, and must
stay False on every path that returns without copying (restore not allowed, or no
backup file on disk). A restore that silently forgets to record itself is
indistinguishable from a skipped restore, which is why the flag is asserted on both
sides of each branch.
"""

from pathlib import Path

import pytest

from frappe_manager.migration_manager.backup_manager import BackupManager


@pytest.fixture
def backup_manager(tmp_path) -> BackupManager:
    """A BackupManager writing only inside tmp_path."""
    return BackupManager(
        name="0.19.0",
        benches_dir=tmp_path / "sites",
        backup_dir=tmp_path / "backups",
    )


def make_source(tmp_path: Path, content: str = "original") -> Path:
    src = tmp_path / "sites" / "test-bench" / "bench_config.toml"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(content)
    return src


class TestRestoreMarksBackupAsRestored:
    def test_forced_restore_records_that_it_ran(self, backup_manager, tmp_path):
        """A restore that copies data back must record is_restored=True."""
        src = make_source(tmp_path)
        backup_data = backup_manager.backup(src, bench_name="test-bench")

        assert backup_data.is_restored is False, "Freshly created backup must not claim to be restored"

        src.write_text("migrated content")
        dest = backup_manager.restore(backup_data, force=True)

        assert dest is not None
        assert src.read_text() == "original", "Restore must put the original content back"
        assert backup_data.is_restored is True, "A completed restore must be recorded on the BackupData"

    def test_unforced_restore_also_records_that_it_ran(self, backup_manager, tmp_path):
        """force only controls deleting the current file first; the flag is still set."""
        src = make_source(tmp_path)
        backup_data = backup_manager.backup(src, bench_name="test-bench")
        src.write_text("migrated content")

        backup_manager.restore(backup_data)

        assert src.read_text() == "original"
        assert backup_data.is_restored is True

    def test_second_restore_keeps_the_flag_set(self, backup_manager, tmp_path):
        """Restoring twice must not un-record the first restore."""
        src = make_source(tmp_path)
        backup_data = backup_manager.backup(src, bench_name="test-bench")

        backup_manager.restore(backup_data, force=True)
        backup_manager.restore(backup_data, force=True)

        assert backup_data.is_restored is True


class TestRestoreSkipPathsDoNotRecordRestore:
    def test_disallowed_restore_leaves_flag_false_and_file_untouched(self, backup_manager, tmp_path):
        """allow_restore=False => no copy, nothing recorded."""
        src = make_source(tmp_path)
        backup_data = backup_manager.backup(src, bench_name="test-bench", allow_restore=False)
        src.write_text("migrated content")

        result = backup_manager.restore(backup_data, force=True)

        assert result is None
        assert src.read_text() == "migrated content", "A disallowed restore must not touch the source"
        assert backup_data.is_restored is False

    def test_missing_backup_file_leaves_flag_false(self, backup_manager, tmp_path):
        """No backup on disk => no copy, nothing recorded."""
        src = make_source(tmp_path)
        backup_data = backup_manager.backup(src, bench_name="test-bench")
        backup_data.real_dest.unlink()
        src.write_text("migrated content")

        result = backup_manager.restore(backup_data, force=True)

        assert result is None
        assert src.read_text() == "migrated content"
        assert backup_data.is_restored is False
