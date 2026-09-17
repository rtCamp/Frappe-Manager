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
