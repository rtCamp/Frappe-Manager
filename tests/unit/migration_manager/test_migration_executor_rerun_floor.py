"""The rerun-only discovery window must stay shut without ``--rerun``.

``MigrationExecutor.execute`` hands ``MigrationDiscovery`` a lower bound, and discovery
is strict (``from_version < migration.version``), so the current release's own migration
class is normally excluded. ``--rerun`` exists to re-run it: when the recorded version
has already caught up with the running one, the lower bound is pulled down to the
release's own dev marker (``0.19.0.dev0`` for ``0.19.x``) -- deliberately not ``0.0.0``,
so older and potentially non-idempotent migrations are NOT dragged back in.

Defended here: that widening is gated on ``--rerun``, and the floor is expressed as a
dev marker rather than computed by arithmetic, which underflowed on any x.0.0 release.
"""

from unittest.mock import Mock, patch

import pytest

from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.migration_manager.version import Version

CURRENT = "0.19.0"
RELEASE_DEV_FLOOR = Version("0.19.0.dev0")


def _run_execute(mock_fm_config, *, rerun: bool, current: str = CURRENT):
    """Execute with benches that report the current version, capturing discovery's bounds."""
    mock_fm_config.get_system_migration_version.return_value = Version(current)

    with (
        patch(
            "frappe_manager.migration_manager.migration_executor.get_current_fm_version",
            return_value=current,
        ),
        patch("frappe_manager.migration_manager.migration_executor.get_logger"),
    ):
        executor = MigrationExecutor(mock_fm_config, rerun=rerun, auto_proceed=True, output_handler=Mock())

        with (
            patch.object(executor, "_check_benches_need_migration", return_value=True),
            patch.object(executor, "_get_minimum_bench_version", return_value=Version(current)),
            patch.object(executor.discovery, "discover_migrations", return_value=[]) as discover,
            patch.object(executor.orchestrator, "execute_migrations"),
            patch.object(executor.error_handler, "finalize_success"),
        ):
            result = executor.execute()

    return result, discover


class TestRerunDiscoveryFloor:
    def test_rerun_pulls_the_lower_bound_down_to_this_releases_dev_floor(self, mock_fm_config):
        result, discover = _run_execute(mock_fm_config, rerun=True)

        assert result is True
        discover.assert_called_once()
        from_version, to_version = discover.call_args.args[0], discover.call_args.args[1]
        # Narrowed to this release only: 0.19.0's migration is included, 0.18.0's is not.
        assert from_version == RELEASE_DEV_FLOOR
        assert from_version > Version("0.18.0")
        assert to_version == Version(CURRENT)

    def test_without_rerun_an_up_to_date_version_is_passed_through_unwidened(self, mock_fm_config):
        result, discover = _run_execute(mock_fm_config, rerun=False)

        assert result is True
        discover.assert_called_once()
        from_version = discover.call_args.args[0]
        # Unchanged, so discovery's strict `<` keeps the current migration out.
        assert from_version == Version(CURRENT)
        assert from_version != RELEASE_DEV_FLOOR

    @pytest.mark.parametrize("current", ["1.0.0", "2.0.0", "1.0.0.dev0"])
    def test_a_major_release_floor_does_not_underflow(self, mock_fm_config, current):
        """A floor computed by decrementing the minor produced `1.-1.9999` on any x.0.0
        release, which `Version` refuses to parse -- `fm services migrate --rerun` died with
        'Invalid version' on a real 1.0.0 host."""
        result, discover = _run_execute(mock_fm_config, rerun=True, current=current)

        assert result is True
        from_version = discover.call_args.args[0]
        base = Version(current).base_version
        assert from_version == Version(f"{base}.dev0")
        assert from_version < Version(base)
