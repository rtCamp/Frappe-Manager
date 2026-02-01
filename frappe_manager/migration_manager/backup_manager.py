import shutil
from dataclasses import dataclass
from datetime import datetime
import time
from pathlib import Path
from typing import Optional, Set, ClassVar
from frappe_manager import CLI_DIR, CLI_BENCHES_DIRECTORY
from frappe_manager.migration_manager.migration_constants import TIMESTAMP_COLLISION_RETRY_DELAY_MS
from frappe_manager.logger import log


@dataclass
class BackupData:
    src: Path
    dest: Path
    bench: Optional[str] = None
    bench_path: Optional[Path] = None
    prefix_timestamp: bool = False
    allow_restore: bool = True
    _is_restored: bool = False

    _used_timestamps: ClassVar[Set[str]] = set()

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
        timestamp = datetime.now().strftime('%d-%b-%y--%H-%M-%S')

        while timestamp in BackupData._used_timestamps:
            time.sleep(TIMESTAMP_COLLISION_RETRY_DELAY_MS)
            timestamp = datetime.now().strftime('%d-%b-%y--%H-%M-%S-%f')[:23]

        BackupData._used_timestamps.add(timestamp)
        return f"{base_name}-{timestamp}"

    def exists(self):
        return self.dest.exists()


CLI_MIGARATIONS_DIR = CLI_DIR / 'backups'


class BackupManager:
    _active_sessions: ClassVar[Set[str]] = set()

    def __init__(
        self,
        name: str,
        backup_group_name: str = 'migrations',
        benches_dir: Path = CLI_BENCHES_DIRECTORY,
        backup_dir: Path = CLI_MIGARATIONS_DIR,
    ):
        self.name = name
        self.backup_group_name = backup_group_name
        self.migration_timestamp = self._generate_unique_session_timestamp()
        self.root_backup_dir: Path = backup_dir / backup_group_name / self.migration_timestamp
        self.benches_dir: Path = benches_dir
        self.backup_dir: Path = self.root_backup_dir / self.name
        self.bench_backup_dir: Path = Path('backups') / backup_group_name / self.migration_timestamp
        self.backups = []
        self.logger = log.get_logger()

        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _generate_unique_session_timestamp(self) -> str:
        timestamp = datetime.now().strftime('%d-%b-%y--%H-%M-%S')

        while timestamp in BackupManager._active_sessions:
            time.sleep(TIMESTAMP_COLLISION_RETRY_DELAY_MS)
            timestamp = datetime.now().strftime('%d-%b-%y--%H-%M-%S-%f')[:23]

        BackupManager._active_sessions.add(timestamp)
        return timestamp

    def backup(
        self,
        src: Path,
        dest: Optional[Path] = None,
        bench_name: Optional[str] = None,
        allow_restore: bool = True,
    ):
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
            # print(f"No backup found at {backup_data.real_dest}")
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
        # print(f"Restored {backup_data.src} from backup")

    def delete(self, backup_data):
        """
        Delete a specific backup.
        """
        if not backup_data.real_dest.exists():
            # print(f"No backup found at {backup_data.real_dest}")
            return None

        shutil.rmtree(backup_data.real_dest)

        self.backups.remove(backup_data)
        # print(f"Deleted backup at {backup_data.real_dest}")

    def delete_all(self):
        """
        Delete all backups.
        """
        for backup_data in self.backups:
            if backup_data.real_dest.exists():
                shutil.rmtree(backup_data.real_dest)
                # print(f"Deleted backup at {backup_data.real_dest}")

        self.backups.clear()
        # print("Deleted all backups")
