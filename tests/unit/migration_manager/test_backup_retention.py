"""Disk-hygiene engine (utils/prune.py) and the no-side-effect contract around it.

Two rulings defended:
- cleanup is COMMAND-triggered only (`fm prune`, `fm services prune`): a successful
  migration prints a size-aware hint and never deletes history (backups ARE the rollback);
- BackupManager creates its session dir lazily: the eager constructor mkdir littered a real
  install's ~/frappe/backups with 21k empty timestamp dirs, one per construction, including
  the thousands the unit suite performs.
"""

import os
import time

import pytest

from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.utils.prune import parse_size, plan_session_prune, stale_sessions


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


class TestStaleSessions:
    def test_orders_by_mtime_never_by_name(self, tmp_path):
        """The DD-Mon-YY timestamp format sorts by day-of-month lexically ('01-Sep' before
        '28-Aug'), so a name sort would prune the wrong sessions. Names here are chosen so
        the lexical order is the REVERSE of the age order."""
        root = _sessions(tmp_path / "migrations", ["z-oldest", "m-older", "c-newer", "a-newest"])

        assert [p.name for p in stale_sessions(root, keep=2)] == ["z-oldest", "m-older"]  # oldest first

    def test_at_or_below_the_limit_nothing_is_stale(self, tmp_path):
        root = _sessions(tmp_path / "migrations", ["one", "two", "three"])

        assert stale_sessions(root, keep=3) == []
        assert stale_sessions(root, keep=5) == []

    def test_a_missing_root_is_a_quiet_noop(self, tmp_path):
        assert stale_sessions(tmp_path / "never-created", keep=3) == []

    def test_stray_files_are_neither_counted_nor_stale(self, tmp_path):
        """Only directories are sessions; a stray file at the root must not consume a keep
        slot or be reported for deletion."""
        root = _sessions(tmp_path / "migrations", ["old", "mid", "new"])
        (root / "README.txt").write_text("hands off")

        assert [p.name for p in stale_sessions(root, keep=2)] == ["old"]

    def test_plan_reports_kept_count_and_stale_size(self, tmp_path):
        root = _sessions(tmp_path / "migrations", ["a", "b", "c"])
        (root / "a" / "dump.sql").write_bytes(b"x" * 2048)
        os.utime(root / "a", (time.time() - 300, time.time() - 300))  # writing the file bumped the dir mtime
        plan = plan_session_prune(root, keep=2)

        assert plan.kept == 2
        assert [p.name for p in plan.stale] == ["a"]
        assert plan.size == 2048


class TestParseSize:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("500K", 500 * 1024), ("10M", 10 * 1024**2), ("1G", 1024**3), ("2048", 2048), ("1.5M", int(1.5 * 1024**2))],
    )
    def test_accepts_the_documented_forms(self, text, expected):
        assert parse_size(text) == expected

    def test_rejects_garbage_with_the_expected_forms_named(self):
        with pytest.raises(ValueError, match="10M"):
            parse_size("ten megabytes")


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
