"""Fixtures shared by the WHOLE suite (unit and integration alike)."""

import itertools

import pytest

from frappe_manager.utils import process_lock

_lock_dir_counter = itertools.count()


@pytest.fixture(scope="session")
def _locks_base(tmp_path_factory):
    return tmp_path_factory.mktemp("locks")


@pytest.fixture(autouse=True)
def isolated_locks_dir(_locks_base, monkeypatch):
    """The host/bench locks are real flocks under CLI_DIR; tests must never grip the
    developer's actual ~/frappe/locks. Two reasons, one per failure mode seen: a test
    would fence off (or contend with) a real fm running on the machine, and a handle
    LEAKED by one test contends same-process with a later test's acquire -- flock treats
    two opens of one file in one process as rivals -- failing tests based on ordering.

    The per-test directory is deliberately NOT created here: ``acquire`` mkdirs on
    demand, and most tests never take a lock, so this fixture stays at attribute-set
    cost across the whole suite.
    """
    monkeypatch.setattr(process_lock, "LOCKS_DIR", _locks_base / str(next(_lock_dir_counter)))


@pytest.fixture(scope="session")
def _backups_base(tmp_path_factory):
    return tmp_path_factory.mktemp("migration-backups")


@pytest.fixture(autouse=True)
def isolated_migration_backups_root(_backups_base, monkeypatch):
    """Retention pruning (MigrationExecutor._prune_backup_sessions) deletes session dirs
    under the host backups root after a successful run -- and executor unit tests exercise
    exactly that path. The root is read from ``backup_manager.CLI_MIGARATIONS_DIR`` at call
    time (function-local import), so pointing the module attribute at a per-test path keeps
    every test's pruning (and any stray BackupManager default) away from the developer's
    real ~/frappe/backups. Same protection class as ``isolated_locks_dir`` above, same
    cost profile: attribute-set only, the directory is never created here (the unit suite
    once littered the real ~/frappe/backups with 21k empty session dirs)."""
    from frappe_manager.migration_manager import backup_manager

    monkeypatch.setattr(backup_manager, "CLI_MIGARATIONS_DIR", _backups_base / str(next(_lock_dir_counter)))
