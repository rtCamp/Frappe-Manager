"""``--skip-backup`` must actually suppress v0.19.0's extra backups.

The v0.19.0 migration rebuilds each bench's runtime, so on top of the parent's basic
backup it copies the files that rebuild regenerates or rewrites: ``supervisor.conf``,
every ``*.fm.supervisor.conf``, and nginx's ``default.conf``. Copying those is exactly
what ``--skip-backup`` (global) and ``--skip-backup-for <bench>`` (per bench) exist to
refuse -- users reach for them when disk is short or the bench is disposable.

Defended here: EITHER refusal is enough to skip, the per-bench list is matched against
this bench's name only, and the parent's basic backup is unaffected by both (it has its
own guards, so this override must not swallow it).
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migrations.migrate_0_19_0 import MigrationV0190

BENCH_NAME = "test-bench"


@pytest.fixture
def bench(tmp_path):
    """A bench dir carrying every file the override knows how to back up."""
    path = tmp_path / "sites" / BENCH_NAME
    config_dir = path / "workspace" / "frappe-bench" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "supervisor.conf").write_text("[supervisord]\n")
    (config_dir / "frappe-bench-frappe-web.fm.supervisor.conf").write_text("[program:web]\n")

    nginx_dir = path / "configs" / "nginx" / "conf" / "conf.d"
    nginx_dir.mkdir(parents=True)
    (nginx_dir / "default.conf").write_text("server {}\n")

    return SimpleNamespace(name=BENCH_NAME, path=path)


@pytest.fixture
def migration():
    """v0.19.0 with a stub backup manager; the parent's backup is stubbed separately."""
    migration = MigrationV0190(output_handler=Mock())
    migration.backup_manager = Mock()
    migration.migration_executor = Mock(skip_backup=False, skip_backup_for=[])
    return migration


def _run(migration, bench):
    with patch.object(MigrationBase, "bench_basic_backup") as parent_backup:
        migration.bench_basic_backup(bench)
    return parent_backup


def _backed_up_names(migration):
    return sorted(call.args[0].name for call in migration.backup_manager.backup.call_args_list)


def test_global_skip_backup_suppresses_the_runtime_backups(migration, bench):
    migration.migration_executor.skip_backup = True
    migration.migration_executor.skip_backup_for = []

    parent_backup = _run(migration, bench)

    migration.backup_manager.backup.assert_not_called()
    parent_backup.assert_called_once_with(bench)


def test_per_bench_skip_backup_suppresses_the_runtime_backups(migration, bench):
    migration.migration_executor.skip_backup = False
    migration.migration_executor.skip_backup_for = [BENCH_NAME]

    parent_backup = _run(migration, bench)

    migration.backup_manager.backup.assert_not_called()
    parent_backup.assert_called_once_with(bench)


def test_skip_list_for_another_bench_does_not_spare_this_one(migration, bench):
    # The list is per bench: a neighbour opting out must not silently disarm the
    # backup of the bench actually being migrated.
    migration.migration_executor.skip_backup = False
    migration.migration_executor.skip_backup_for = ["some-other-bench"]

    _run(migration, bench)

    assert _backed_up_names(migration) == [
        "default.conf",
        "frappe-bench-frappe-web.fm.supervisor.conf",
        "supervisor.conf",
    ]


def test_without_any_skip_every_regenerated_file_is_backed_up(migration, bench):
    parent_backup = _run(migration, bench)

    assert _backed_up_names(migration) == [
        "default.conf",
        "frappe-bench-frappe-web.fm.supervisor.conf",
        "supervisor.conf",
    ]
    # Every copy is attributed to this bench, which is how rollback finds them.
    assert {call.kwargs["bench_name"] for call in migration.backup_manager.backup.call_args_list} == {BENCH_NAME}
    parent_backup.assert_called_once_with(bench)
