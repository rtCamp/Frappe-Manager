"""What `fm prune` claims it reclaimed must be what it actually freed.

The `Done : ~N reclaimed` line summed only the LIVE files being rotated. Those files shrink
rather than disappear, so a run whose entire job was deleting stale archives reported
"~0 B reclaimed" while really freeing megabytes.
"""

from frappe_manager.utils.prune import LogPrune, LogRotation


def _archive(tmp_path, name: str, size: int):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def test_dropped_archives_count_toward_the_reclaimed_total(tmp_path):
    """The bytes a prune frees are the archives it deletes, not the live files it truncates."""
    plan = LogPrune(archives_to_drop=[_archive(tmp_path, "fm.log.1.gz", 1000)])

    assert plan.rotate_size == 0
    assert plan.drop_size == 1000


def test_archives_dropped_alongside_a_rotation_are_counted_too(tmp_path):
    """A rotation carries its own stale archives; they free disk exactly like standalone ones."""
    rotation = LogRotation(
        path=tmp_path / "fm.log",
        size=500,
        archives_to_drop=[_archive(tmp_path, "fm.log.2.gz", 300)],
    )
    plan = LogPrune(rotations=[rotation], archives_to_drop=[_archive(tmp_path, "web.log.1.gz", 200)])

    assert plan.drop_count == 2
    assert plan.drop_size == 500


def test_an_archive_already_gone_does_not_break_the_total(tmp_path):
    """The plan is built before the delete, and a concurrent logrotate may win the race."""
    plan = LogPrune(archives_to_drop=[tmp_path / "vanished.log.1.gz"])

    assert plan.drop_size == 0
