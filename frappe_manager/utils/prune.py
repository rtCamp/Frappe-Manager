"""Disk-hygiene engine shared by `fm prune` and `fm services prune`.

Three kinds of reclaimable state, one vocabulary:

- **Backup sessions**: the timestamped directories BackupManager groups one run's backups
  under (`backups/migrations/<ts>/`, `backups/workers/<ts>/`, and the host-tier equivalents).
  Ordered by directory mtime, NEVER by name: the ``DD-Mon-YY--HH-MM-SS`` format sorts by
  day-of-month lexically, so a name sort would prune the wrong sessions twelve months a year.
- **Log rotation**: gzip-copy then TRUNCATE IN PLACE. The writing processes (gunicorn,
  workers, nginx) hold their log files open and never reopen them, so renaming a file out
  from under them silently stops the logging -- the same reason the documented logrotate
  config mandates ``copytruncate``. Archives are `<name>.<ts>.gz` beside the live file,
  kept to a count, oldest deleted first.
- Nothing here decides WHEN to run: cleanup is command-triggered only (`fm prune`,
  `fm services prune`), never a side effect of another operation. Migrations print a
  size-aware hint instead of pruning.

Every function is dry-run capable and returns what it did (or would do) with sizes, so the
commands can report before-the-fact and deletions are never silent.
"""

import gzip
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?)B?\s*$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(text: str) -> int:
    """`'10M'` -> bytes. Accepts bare bytes, K/M/G/T suffixes, optional trailing B."""
    match = _SIZE_RE.match(str(text))
    if not match:
        raise ValueError(f"Not a size: {text!r} (expected e.g. '500K', '10M', '1G')")
    number, unit = match.groups()
    return int(float(number) * _SIZE_UNITS[unit.upper()])


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def dir_size(path: Path) -> int:
    """Recursive byte size; missing paths and races read as 0."""
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total



@dataclass
class SessionPrune:
    """One backups root's plan/result: the stale session dirs beyond the newest ``keep``."""

    root: Path
    kept: int
    stale: list[Path]  # oldest first
    size: int  # bytes across all stale sessions

    @property
    def count(self) -> int:
        return len(self.stale)


def stale_sessions(root: Path, keep: int) -> list[Path]:
    """Session dirs under ``root`` beyond the newest ``keep``, oldest first, by mtime.

    Only directories are sessions; stray files neither consume a keep slot nor get deleted.
    """
    if not root.is_dir():
        return []
    sessions = [p for p in root.iterdir() if p.is_dir()]
    sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return list(reversed(sessions[keep:]))


def plan_session_prune(root: Path, keep: int) -> SessionPrune:
    stale = stale_sessions(root, keep)
    kept = 0
    if root.is_dir():
        kept = sum(1 for p in root.iterdir() if p.is_dir()) - len(stale)
    return SessionPrune(root=root, kept=kept, stale=stale, size=sum(dir_size(p) for p in stale))


def execute_session_prune(plan: SessionPrune) -> None:
    for stale in plan.stale:
        shutil.rmtree(stale, ignore_errors=True)



@dataclass
class LogRotation:
    """One live log file's plan/result."""

    path: Path
    size: int
    archives_to_drop: list[Path] = field(default_factory=list)


@dataclass
class LogPrune:
    """A whole run's log plan: files to rotate plus stale archives of already-small files."""

    rotations: list[LogRotation] = field(default_factory=list)
    # Archives beyond keep for files NOT being rotated this run (they were rotated before).
    archives_to_drop: list[Path] = field(default_factory=list)

    @property
    def rotate_size(self) -> int:
        return sum(r.size for r in self.rotations)

    @property
    def drop_count(self) -> int:
        return len(self.archives_to_drop) + sum(len(r.archives_to_drop) for r in self.rotations)


def _archives_of(log_file: Path) -> list[Path]:
    """This file's rotation archives, newest first by mtime."""
    archives = list(log_file.parent.glob(f"{log_file.name}.*.gz"))
    archives.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return archives


def plan_log_prune(log_dirs: list[Path], over_bytes: int, keep_archives: int) -> LogPrune:
    """Plan rotation for every ``*.log`` in ``log_dirs`` larger than ``over_bytes``.

    Archive retention counts the archive ABOUT to be created: a file being rotated with
    ``keep_archives`` existing archives drops its oldest so the total stays at the limit.
    """
    plan = LogPrune()
    for log_dir in log_dirs:
        if not log_dir.is_dir():
            continue
        for log_file in sorted(log_dir.glob("*.log")):
            if log_file.is_symlink() or not log_file.is_file():
                continue
            archives = _archives_of(log_file)
            size = log_file.stat().st_size
            if size > over_bytes:
                # The new archive takes one slot.
                overflow = archives[max(keep_archives - 1, 0) :]
                plan.rotations.append(LogRotation(path=log_file, size=size, archives_to_drop=list(reversed(overflow))))
            elif len(archives) > keep_archives:
                plan.archives_to_drop.extend(reversed(archives[keep_archives:]))
    return plan


def execute_log_prune(plan: LogPrune) -> None:
    """Rotate and trim. gzip-copy + in-place truncate, never rename: see the module docstring."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for rotation in plan.rotations:
        archive = rotation.path.parent / f"{rotation.path.name}.{stamp}.gz"
        with rotation.path.open("rb") as plain, gzip.open(archive, "wb") as compressed:
            shutil.copyfileobj(plain, compressed)
        # Truncate the SAME inode the writers hold open; a rename would orphan their fd.
        with rotation.path.open("r+b") as live:
            live.truncate(0)
        for old in rotation.archives_to_drop:
            old.unlink(missing_ok=True)
    for old in plan.archives_to_drop:
        old.unlink(missing_ok=True)



def host_prune_settings():
    """The host `[prune]` table, for callers with no FMConfigManager in hand (the bench
    info card). Falls back to the model defaults when the file is unreadable, because a
    status row must never be the thing that breaks `fm info`."""
    from frappe_manager.metadata_manager import FMConfigManager, FMPruneConfig

    try:
        return FMConfigManager.import_from_toml().prune
    except Exception:
        return FMPruneConfig()


def summarize_disk_status(
    *,
    session_roots: list[Path],
    log_dirs: list[Path],
    keep_sessions: int,
    keep_archives: int,
    over_bytes: int,
    releases_beyond: int = 0,
) -> tuple[str, bool]:
    """``(plain-text summary, actionable)`` for the info cards' `disk` row.

    Pure stats and globs -- no docker, no deletion, cheap enough for every `fm info`.
    The clean wording is deliberately FULL (what was counted, against which threshold),
    so "within retention" reads as a performed check rather than a skipped one. The
    caller appends the command name and applies markup.
    """
    stale_count = 0
    stale_size = 0
    kept = 0
    for root in session_roots:
        plan = plan_session_prune(root, keep_sessions)
        stale_count += plan.count
        stale_size += plan.size
        kept += plan.kept

    log_plan = plan_log_prune(log_dirs, over_bytes, keep_archives)

    parts: list[str] = []
    if releases_beyond:
        parts.append(f"{releases_beyond} release(s) beyond keep")
    if stale_count:
        parts.append(f"{stale_count} backup session(s) beyond keep {keep_sessions} ({format_size(stale_size)})")
    if log_plan.rotations:
        parts.append(f"{len(log_plan.rotations)} log(s) over {format_size(over_bytes)} ({format_size(log_plan.rotate_size)})")

    if parts:
        return " · ".join(parts), True
    return f"within retention ({kept} backup session(s) kept, logs under {format_size(over_bytes)})", False
