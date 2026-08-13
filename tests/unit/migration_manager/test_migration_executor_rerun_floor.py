"""The rerun-only discovery window must stay shut without ``--rerun``.

``MigrationExecutor.execute`` hands ``MigrationDiscovery`` a lower bound, and discovery
is strict (``from_version < migration.version``), so the current release's own migration
class is normally excluded. ``--rerun`` exists to re-run it: when the recorded version
has already caught up with the running one, the lower bound is pulled down to the
previous minor's ceiling (``0.18.9999`` for ``0.19.x``) -- deliberately not ``0.0.0``,
so older and potentially non-idempotent migrations are NOT dragged back in.

Defended here: that widening is gated on ``--rerun``. Without it, an up-to-date
effective version must be passed to discovery unchanged, or every ordinary ``fm``
invocation whose benches need attention would re-apply the current release's migration.
"""

from unittest.mock import Mock, patch

from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.migration_manager.version import Version

CURRENT = "0.19.0"
PREVIOUS_MINOR_FLOOR = Version("0.18.9999")


def _run_execute(mock_fm_config, *, rerun: bool):
    """Execute with benches that report the current version, capturing discovery's bounds."""
    mock_fm_config.version = Version(CURRENT)

    with (
        patch(
            "frappe_manager.migration_manager.migration_executor.get_current_fm_version",
            return_value=CURRENT,
        ),
        patch("frappe_manager.migration_manager.migration_executor.get_logger"),
    ):
        executor = MigrationExecutor(mock_fm_config, rerun=rerun, auto_proceed=True, output_handler=Mock())

        with (
            patch.object(executor, "_check_benches_need_migration", return_value=True),
            patch.object(executor, "_get_minimum_bench_version", return_value=Version(CURRENT)),
            patch.object(executor.discovery, "discover_migrations", return_value=[]) as discover,
            patch.object(executor.orchestrator, "execute_migrations"),
            patch.object(executor.error_handler, "finalize_success"),
        ):
            result = executor.execute()

    return result, discover


class TestRerunDiscoveryFloor:
    def test_rerun_pulls_the_lower_bound_down_to_the_previous_minor_floor(self, mock_fm_config):
        result, discover = _run_execute(mock_fm_config, rerun=True)

        assert result is True
        discover.assert_called_once()
        from_version, to_version = discover.call_args.args[0], discover.call_args.args[1]
        # Narrowed to this release only: 0.19.0's migration is included, 0.18.0's is not.
        assert from_version == PREVIOUS_MINOR_FLOOR
        assert to_version == Version(CURRENT)

    def test_without_rerun_an_up_to_date_version_is_passed_through_unwidened(self, mock_fm_config):
        result, discover = _run_execute(mock_fm_config, rerun=False)

        assert result is True
        discover.assert_called_once()
        from_version = discover.call_args.args[0]
        # Unchanged, so discovery's strict `<` keeps the current migration out.
        assert from_version == Version(CURRENT)
        assert from_version != PREVIOUS_MINOR_FLOOR
