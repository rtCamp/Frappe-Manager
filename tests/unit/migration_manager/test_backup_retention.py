"""Backup retention (P5): sessions are pruned to a fixed count, on SUCCESS only.

Two failure modes defended:
- unbounded growth: every migration run permanently kept its timestamped session (config
  copies plus a full DB dump per site), and nothing anywhere deleted any of it;
- empty-dir spam: BackupManager's constructor eagerly mkdir'd its session dir, so every
  construction -- including the thousands the unit suite performs -- littered the real
  ~/frappe/backups with empty timestamp dirs (21k observed on a dev machine).
"""

import os
import time

from frappe_manager.migration_manager.backup_manager import (
    MIGRATION_BACKUP_KEEP_SESSIONS,
    BackupManager,
    prune_old_backup_sessions,
)


def _sessions(root, names):
    """Create session dirs with strictly increasing mtimes in `names` order."""
    root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for offset, name in enumerate(names):
        d = root / name
        d.mkdir()
        stamp = now - (len(names) - offset) * 100
        os.utime(d, (stamp, stamp))
    return root


class TestPruneOldBackupSessions:
    def test_keeps_the_newest_by_mtime_never_by_name(self, tmp_path):
        """The DD-Mon-YY timestamp format sorts by day-of-month lexically ('01-Sep' before
        '28-Aug'), so a name sort would prune the wrong sessions. Names here are chosen so
        the lexical order is the REVERSE of the age order."""
        root = _sessions(tmp_path / "migrations", ["z-oldest", "m-older", "c-newer", "a-newest"])

        removed = prune_old_backup_sessions(root, keep=2)

        assert removed == ["z-oldest", "m-older"]  # oldest first, by mtime
        assert sorted(p.name for p in root.iterdir()) == ["a-newest", "c-newer"]

    def test_at_or_below_the_limit_nothing_is_removed(self, tmp_path):
        root = _sessions(tmp_path / "migrations", ["one", "two", "three"])

        assert prune_old_backup_sessions(root, keep=3) == []
        assert prune_old_backup_sessions(root, keep=5) == []
        assert len(list(root.iterdir())) == 3

    def test_a_missing_root_is_a_quiet_noop(self, tmp_path):
        assert prune_old_backup_sessions(tmp_path / "never-created") == []

    def test_stray_files_are_neither_counted_nor_deleted(self, tmp_path):
        """Only directories are sessions; a stray file at the root must not consume a keep
        slot or be deleted."""
        root = _sessions(tmp_path / "migrations", ["old", "mid", "new"])
        (root / "README.txt").write_text("hands off")

        removed = prune_old_backup_sessions(root, keep=2)

        assert removed == ["old"]
        assert (root / "README.txt").exists()

    def test_default_keep_is_three(self, tmp_path):
        root = _sessions(tmp_path / "migrations", ["a", "b", "c", "d", "e"])

        removed = prune_old_backup_sessions(root)

        assert MIGRATION_BACKUP_KEEP_SESSIONS == 3
        assert len(removed) == 2
        assert len(list(root.iterdir())) == 3


class TestLazySessionDirectory:
    def test_constructing_a_manager_creates_nothing_on_disk(self, tmp_path):
        """Construction is not a promise anything will be backed up; the eager mkdir here
        is what produced 21k empty session dirs on a real install."""
        backups_root = tmp_path / "backups"

        manager = BackupManager(name="0.21.0", benches_dir=tmp_path / "sites", backup_dir=backups_root)

        assert not backups_root.exists()
        assert manager.backup_dir.is_relative_to(backups_root)  # path computed, not created

    def test_the_first_backup_creates_the_session_dir(self, tmp_path):
        backups_root = tmp_path / "backups"
        src = tmp_path / "docker-compose.yml"
        src.write_text("services: {}\n")

        manager = BackupManager(name="0.21.0", benches_dir=tmp_path / "sites", backup_dir=backups_root)
        manager.backup(src)

        assert (manager.backup_dir / "docker-compose.yml").read_text() == "services: {}\n"
