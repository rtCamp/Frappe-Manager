"""
Defends the cap on ``MigrationOrchestrator.rollback_version``.

``execute_migrations()`` advances ``rollback_version`` to each successfully-applied
step's own version. A migration's version can be AHEAD of the running fm's
``current_version`` (a migration targets a future release, not necessarily this
build), so letting it through unclamped lets a failure stamp the services-tier ledger
past where a successful run would ever leave it -- the next real migration then never
runs again. The fix floors every advance at ``current_version``, inside the loop,
because ``rollback_version`` is also read by ``rollback_migrations()`` (to decide what
to undo) and by the CLI-downgrade instructions; capping only where it gets stamped
would leave those two still poisoned.
"""

from unittest.mock import MagicMock, Mock

import pytest

from frappe_manager.migration_manager.migration_orchestrator import MigrationOrchestrator
from frappe_manager.migration_manager.version import Version


class FakeMigration:
    """Just enough surface for execute_migrations(): version, up(), get_rollback_version()."""

    def __init__(self, version: Version, fails: bool = False):
        self.version = version
        self.fails = fails

    def up(self):
        if self.fails:
            raise RuntimeError("deliberately failing migration")

    def get_rollback_version(self):
        return self.version

    def down(self):
        pass


def make_orchestrator(migrations, current_version: Version) -> MigrationOrchestrator:
    """An orchestrator whose executor is fully stubbed; nothing touches disk or docker."""
    executor = MagicMock()
    executor.output = MagicMock()
    executor.migrations = list(migrations)
    executor.current_version = current_version
    orchestrator = MigrationOrchestrator(executor)
    orchestrator.logger = Mock()
    return orchestrator


class TestRollbackVersionNeverExceedsCurrentVersion:
    def test_successful_step_ahead_of_current_version_is_capped(self):
        """A step whose own version is ahead of current_version must not inflate rollback_version."""
        current_version = Version("1.0.0.dev0")
        step_ahead_of_current = FakeMigration(Version("1.0.0"))
        failing_step = FakeMigration(Version("1.0.1"), fails=True)

        orchestrator = make_orchestrator([step_ahead_of_current, failing_step], current_version)

        with pytest.raises(RuntimeError):
            orchestrator.execute_migrations()

        assert orchestrator.executor.rollback_version == current_version, (
            "rollback_version must never be stamped past the running fm's own current_version"
        )

    def test_successful_step_below_current_version_still_advances_normally(self):
        """The cap must not hold rollback_version below a step that is legitimately behind current."""
        current_version = Version("2.0.0")
        early_step = FakeMigration(Version("1.0.0"))
        failing_step = FakeMigration(Version("1.5.0"), fails=True)

        orchestrator = make_orchestrator([early_step, failing_step], current_version)

        with pytest.raises(RuntimeError):
            orchestrator.execute_migrations()

        assert orchestrator.executor.rollback_version == Version("1.0.0")
