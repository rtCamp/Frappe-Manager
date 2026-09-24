import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from frappe_manager import CLI_BENCHES_DIRECTORY, CLI_DIR
from frappe_manager.logger import get_logger
from frappe_manager.migration_manager.migration_constants import TIMESTAMP_COLLISION_RETRY_DELAY_SECONDS


@dataclass
class BackupData:
    src: Path
    dest: Path
    bench: str | None = None
    bench_path: Path | None = None
    prefix_timestamp: bool = False
    allow_restore: bool = True
    _is_restored: bool = False

    _used_timestamps: ClassVar[set[str]] = set()

    @property
    def is_restored(self) -> bool:
        return self._is_restored

    @is_restored.setter
    def is_restored(self, v: bool) -> None:
        self._is_restored = v

    def __post_init__(self):
        file_name = self.dest.name

        if self.prefix_timestamp:
            file_name = self._generate_unique_timestamp_filename()

        self.real_dest = self.dest.parent
        self.real_dest: Path = self.real_dest / file_name

    def _generate_unique_timestamp_filename(self) -> str:
        base_name = self.dest.name
        timestamp = datetime.now().strftime("%d-%b-%y--%H-%M-%S")

        while timestamp in BackupData._used_timestamps:
            time.sleep(TIMESTAMP_COLLISION_RETRY_DELAY_SECONDS)
            timestamp = datetime.now().strftime("%d-%b-%y--%H-%M-%S-%f")[:23]

        BackupData._used_timestamps.add(timestamp)
        return f"{base_name}-{timestamp}"

    def exists(self):
        return self.dest.exists()


CLI_MIGARATIONS_DIR = CLI_DIR / "backups"

# No retention machinery lives here: cleanup is command-triggered only (`fm prune`,
# `fm services prune`; engine in utils/prune.py). Migrations print a size-aware hint
# after success -- they never delete history, because backups ARE the rollback.


class BackupManager:
    _active_sessions: ClassVar[set[str]] = set()

    def __init__(
        self,
        name: str,
        backup_group_name: str = "migrations",
        benches_dir: Path = CLI_BENCHES_DIRECTORY,
        # None -> CLI_MIGARATIONS_DIR resolved at CALL time (module global), never a
        # def-time default: the test suite repoints the module attribute to keep managers
        # and pruning away from the real ~/frappe/backups, and a def-time binding is deaf
        # to that.
        backup_dir: Path | None = None,
        skip_file_backups: bool = False,
    ):
        # The kind-scoped backup policy is enforced HERE, at the one chokepoint every
        # file backup of every migration flows through, so no migration - past or
        # future - has to know the policy exists. Database dumps do not pass through
        # this class (they are taken by the DB managers); their chokepoint is
        # MigrationBase.bench_db_backup.
        self.skip_file_backups = skip_file_backups
        self.name = name
        self.backup_group_name = backup_group_name
        self.migration_timestamp = self._generate_unique_session_timestamp()
        self.root_backup_dir: Path = (backup_dir or CLI_MIGARATIONS_DIR) / backup_group_name / self.migration_timestamp
        self.benches_dir: Path = benches_dir
        self.backup_dir: Path = self.root_backup_dir / self.name
        self.bench_backup_dir: Path = Path("backups") / backup_group_name / self.migration_timestamp
        self.backups = []
        self.new_files = []  # Track newly created files for cleanup on rollback
        self.logger = get_logger(component="migration")
        # The session directory is created LAZILY, by the first actual backup (backup()
        # mkdirs its dest parent; the 1.0.0 engine dump guards its own): constructing a
        # manager is not a promise anything will be backed up, and the eager mkdir here
        # littered real installs with thousands of empty timestamp dirs -- every
        # MigrationBase.init() and worker regeneration builds one of these, including the
        # ones the unit suite builds by the thousand against the REAL ~/frappe/backups.

    def _generate_unique_session_timestamp(self) -> str:
        timestamp = datetime.now().strftime("%d-%b-%y--%H-%M-%S")

        while timestamp in BackupManager._active_sessions:
            time.sleep(TIMESTAMP_COLLISION_RETRY_DELAY_SECONDS)
            timestamp = datetime.now().strftime("%d-%b-%y--%H-%M-%S-%f")[:23]

        BackupManager._active_sessions.add(timestamp)
        return timestamp

    def backup(
        self,
        src: Path,
        dest: Path | None = None,
        bench_name: str | None = None,
        allow_restore: bool = True,
    ):
        if self.skip_file_backups:
            self.logger.debug(f"Backup skipped by policy (--skip-backup/--skip-config-backup): {src}")
            return None
        if not src.exists():
            return None

        backup_dest = dest
        if not backup_dest:
            backup_dest = self.backup_dir / src.name

            if bench_name:
                backup_dest = self.benches_dir / bench_name / self.bench_backup_dir / self.name / src.name

                if self.name == self.backup_group_name:
                    backup_dest = self.benches_dir / bench_name / self.bench_backup_dir / src.name

        backup_data = BackupData(src, backup_dest, bench=bench_name, allow_restore=allow_restore)

        if not backup_data.real_dest.parent.exists():
            backup_data.real_dest.parent.mkdir(parents=True, exist_ok=True)

        self.logger.debug(f"Backup: {backup_data.src} => {backup_data.real_dest} ")

        if src.is_dir():
            shutil.copytree(backup_data.src, backup_data.real_dest)
        else:
            shutil.copy2(backup_data.src, backup_data.real_dest)

        self.backups.append(backup_data)

        return backup_data

    def restore(self, backup_data, force=False):
        """
        Restore a file from a backup.
        """
        if not backup_data.allow_restore:
            return None

        if not backup_data.real_dest.exists():
            return None

        if force:
            self.logger.debug(f"Restore: {backup_data.real_dest} => {backup_data.src} ")
            if backup_data.src.exists():
                if backup_data.src.is_dir():
                    shutil.rmtree(backup_data.src)
                else:
                    backup_data.src.unlink()

        dest = shutil.copy(backup_data.real_dest, backup_data.src)

        backup_data.is_restored = True

        return dest

    def delete(self, backup_data):
        """
        Delete a specific backup.
        """
        if not backup_data.real_dest.exists():
            return

        shutil.rmtree(backup_data.real_dest)

        self.backups.remove(backup_data)

    def track_new_file(self, filepath: Path):
        """
        Track a newly created file so it can be cleaned up during rollback.

        Args:
            filepath: Path to the newly created file.
        """
        if filepath.exists():
            self.new_files.append(filepath)
            self.logger.debug(f"Tracked new file for rollback cleanup: {filepath}")

    def cleanup_new_files(self):
        """
        Delete all tracked newly created files (used during rollback).
        """
        for filepath in self.new_files:
            if filepath.exists():
                if filepath.is_dir():
                    shutil.rmtree(filepath)
                else:
                    filepath.unlink()
                self.logger.debug(f"Cleaned up new file: {filepath}")

        self.new_files.clear()
