"""
Tests for bench migration-version stamping in ``fm migrate``.

After a successful migration run, every targeted bench that did not fail must be
stamped to the *running* fm version — including benches that no migration
touched. Keying the stamp off the last applied migration version left benches
pinned to an older version forever, so every later command kept demanding a
migration that did nothing.
"""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import typer

from frappe_manager.commands.migrate import migrate
from frappe_manager.migration_manager.version import Version

RUNNING_FM_VERSION = "0.20.0.dev0"


def make_bench(benches_dir: Path, name: str) -> Path:
    """Create a bench directory that looks migratable (has a bench config)."""
    bench_path = benches_dir / name
    bench_path.mkdir(parents=True)
    (bench_path / "bench_config.toml").write_text("")
    return bench_path


def bench_entry(last_migration_version: str = "0.19.1", exception=None, traceback=None):
    """Build a ``MigrationExecutor.migrate_benches`` entry for one bench."""
    return {
        "object": Mock(),
        "exception": exception,
        "last_migration_version": Version(last_migration_version),
        "traceback": traceback,
    }


def run_migrate(ctx, benches_dir: Path, migrate_benches: dict, **overrides):
    """
    Call ``migrate()`` directly with the stamping collaborators patched.

    Returns the patched ``set_bench_migration_version`` mock.
    """
    params = {
        "benchname": None,
        "all_benches": False,
        "skip_backup": False,
        "skip_backup_for": None,
        "exclude_bench": None,
        "auto_proceed": True,
        "rerun": False,
        "on_failure": None,
    }
    params.update(overrides)

    executor = Mock()
    executor.execute.return_value = True
    executor.migrate_benches = migrate_benches

    with (
        patch("frappe_manager.commands.migrate.CLI_BENCHES_DIRECTORY", benches_dir),
        patch("frappe_manager.commands.migrate.MigrationExecutor", return_value=executor),
        patch("frappe_manager.commands.migrate.get_current_fm_version", return_value=RUNNING_FM_VERSION),
        patch("frappe_manager.commands.migrate.set_bench_migration_version") as mock_stamp,
    ):
        migrate(ctx, **params)

    return mock_stamp


@pytest.fixture
def benches_dir(tmp_path):
    """Stand-in for CLI_BENCHES_DIRECTORY."""
    path = tmp_path / "benches"
    path.mkdir()
    return path


@pytest.fixture
def ctx():
    """Typer context carrying an FM config manager on an older system version."""
    context = MagicMock(spec=typer.Context)
    fm_config_manager = Mock()
    fm_config_manager.get_system_migration_version.return_value = Version("0.19.1")
    context.obj = {"fm_config_manager": fm_config_manager}
    return context


class TestBenchStampedToRunningVersion:
    """A successful run stamps the running fm version, whatever migrations ran."""

    def test_older_migration_version_still_stamps_running_version(self, ctx, benches_dir):
        """
        Regression: last migration was 0.19.1 while fm runs 0.20.0.dev0.

        The bench must be stamped to 0.20.0.dev0 — not to the migration's own
        version, and not skipped. Skipping here is what made `fm start` keep
        demanding a migration.
        """
        bench_path = make_bench(benches_dir, "test.local")

        stamp = run_migrate(
            ctx,
            benches_dir,
            {"test.local": bench_entry(last_migration_version="0.19.1")},
            benchname="test.local",
        )

        stamp.assert_called_once_with(bench_path, Version(RUNNING_FM_VERSION))
        stamped_path, stamped_version = stamp.call_args[0]
        assert stamped_path == bench_path
        assert str(stamped_version) == RUNNING_FM_VERSION

    def test_no_migration_ran_still_stamps_running_version(self, ctx, benches_dir):
        """A release shipping no migration for the bench still advances its stamp."""
        bench_path = make_bench(benches_dir, "test.local")

        stamp = run_migrate(ctx, benches_dir, {}, benchname="test.local")

        stamp.assert_called_once_with(bench_path, Version(RUNNING_FM_VERSION))

    def test_matching_versions_still_stamps(self, ctx, benches_dir):
        """The ordinary path (migration version == running version) is unchanged."""
        bench_path = make_bench(benches_dir, "test.local")

        stamp = run_migrate(
            ctx,
            benches_dir,
            {"test.local": bench_entry(last_migration_version=RUNNING_FM_VERSION)},
            benchname="test.local",
        )

        stamp.assert_called_once_with(bench_path, Version(RUNNING_FM_VERSION))


class TestFailedBenchNotStamped:
    """A bench whose migration raised must keep its old version."""

    def test_bench_with_exception_is_not_stamped(self, ctx, benches_dir):
        make_bench(benches_dir, "test.local")

        stamp = run_migrate(
            ctx,
            benches_dir,
            {"test.local": bench_entry(exception=Exception("boom"), traceback="traceback")},
            benchname="test.local",
        )

        stamp.assert_not_called()

    def test_failure_does_not_block_stamping_of_healthy_benches(self, ctx, benches_dir):
        broken_path = make_bench(benches_dir, "broken.local")
        healthy_path = make_bench(benches_dir, "healthy.local")

        stamp = run_migrate(
            ctx,
            benches_dir,
            {
                "broken.local": bench_entry(exception=Exception("boom"), traceback="traceback"),
                "healthy.local": bench_entry(last_migration_version="0.19.1"),
            },
            all_benches=True,
        )

        stamped_paths = [call.args[0] for call in stamp.call_args_list]
        assert stamped_paths == [healthy_path]
        assert broken_path not in stamped_paths
        assert stamp.call_args_list[0].args[1] == Version(RUNNING_FM_VERSION)


class TestExcludedBenchNotStamped:
    """--exclude-bench keeps a bench out of target_benches entirely."""

    def test_excluded_bench_is_never_stamped(self, ctx, benches_dir):
        included_path = make_bench(benches_dir, "included.local")
        excluded_path = make_bench(benches_dir, "excluded.local")

        stamp = run_migrate(
            ctx,
            benches_dir,
            {"included.local": bench_entry(last_migration_version="0.19.1")},
            all_benches=True,
            exclude_bench="excluded.local",
        )

        stamped_paths = [call.args[0] for call in stamp.call_args_list]
        assert stamped_paths == [included_path]
        assert excluded_path not in stamped_paths
        assert stamp.call_args_list[0].args[1] == Version(RUNNING_FM_VERSION)
