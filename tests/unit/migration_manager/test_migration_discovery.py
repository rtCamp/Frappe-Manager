"""
Tests for MigrationDiscovery version-range selection.

Contract defended here: a migration runs only when its version is in the
half-open range ``(from_version, normalized_to_version]``.

The boundaries are the whole point:
- ``from_version`` is EXCLUSIVE — the migration the bench is already on must never
  re-run. A ``<=`` there would replay an already-applied migration.
- ``to_version`` is INCLUSIVE — the migration for the release we are upgrading to
  must run, including when the running version is a dev pre-release of it
  (``0.19.0.dev0`` normalizes to ``0.19.0``).
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from frappe_manager.migration_manager.migration_discovery import MigrationDiscovery
from frappe_manager.migration_manager.version import Version

DISCOVERY_MODULE = "frappe_manager.migration_manager.migration_discovery"


def make_migration_class(version_str: str) -> type:
    """A minimal class that satisfies MigrationDiscovery's duck-typing checks."""

    class FakeMigration:
        version = Version(version_str)

        def __init__(self, output_handler=None):
            self.output = output_handler
            self.version = Version(version_str)
            self.migration_executor = None

        def up(self):
            return True

        def down(self):
            return True

        def set_migration_executor(self, migration_executor):
            self.migration_executor = migration_executor

    return FakeMigration


@pytest.fixture
def discovery():
    with patch(f"{DISCOVERY_MODULE}.get_logger"):
        return MigrationDiscovery(migrations_path=Path("/nonexistent/migrations"), output_handler=Mock())


def discover(discovery, available_versions, from_version, to_version):
    """Run discover_migrations against a fake set of migration modules."""
    modules = {f"migrate_{v.replace('.', '_')}": SimpleNamespace(M=make_migration_class(v)) for v in available_versions}

    with (
        patch(
            f"{DISCOVERY_MODULE}.pkgutil.iter_modules",
            return_value=[(None, name, False) for name in modules],
        ),
        patch(
            f"{DISCOVERY_MODULE}.importlib.import_module",
            side_effect=lambda rel, _pkg=None: modules[rel.rsplit(".", 1)[-1]],
        ),
    ):
        found = discovery.discover_migrations(
            from_version=Version(from_version),
            to_version=Version(to_version),
            migration_executor=Mock(),
        )

    return [m.version.version for m in found]


class TestDiscoveryVersionRange:
    def test_migration_equal_to_from_version_is_excluded(self, discovery):
        """The already-applied migration must NOT run again (from_version is exclusive)."""
        selected = discover(discovery, ["0.19.0", "0.20.0"], from_version="0.19.0", to_version="0.20.0")

        assert selected == ["0.20.0"]

    def test_equal_from_and_to_versions_select_nothing(self, discovery):
        """Nothing to do when the bench is already at the target version."""
        selected = discover(discovery, ["0.19.0", "0.20.0"], from_version="0.19.0", to_version="0.19.0")

        assert selected == []

    def test_migration_equal_to_to_version_is_included(self, discovery):
        """to_version is inclusive: the target release's migration must run."""
        selected = discover(discovery, ["0.19.0", "0.20.0"], from_version="0.18.0", to_version="0.19.0")

        assert selected == ["0.19.0"]

    def test_full_range_is_included_and_sorted(self, discovery):
        selected = discover(discovery, ["0.20.0", "0.19.0"], from_version="0.18.0", to_version="0.20.0")

        assert selected == ["0.19.0", "0.20.0"]

    def test_migration_above_to_version_is_excluded(self, discovery):
        selected = discover(discovery, ["0.19.0", "0.20.0"], from_version="0.18.0", to_version="0.19.0")

        assert "0.20.0" not in selected

    def test_dev_target_version_includes_its_release_migration(self, discovery):
        """0.19.0.dev0 normalizes to 0.19.0, so migration 0.19.0 is in range."""
        selected = discover(discovery, ["0.19.0", "0.20.0"], from_version="0.18.0", to_version="0.19.0.dev0")

        assert selected == ["0.19.0"]

    def test_dev_from_version_still_excludes_nothing_extra(self, discovery):
        """from_version is not normalized: 0.19.0.dev0 < 0.19.0, so 0.19.0 still runs."""
        selected = discover(discovery, ["0.19.0"], from_version="0.19.0.dev0", to_version="0.19.0")

        assert selected == ["0.19.0"]


class TestShouldIncludeMigrationBoundaries:
    """Direct boundary matrix for the range predicate."""

    @pytest.mark.parametrize(
        ("migration_version", "from_version", "to_version", "expected"),
        [
            ("0.19.0", "0.19.0", "0.20.0", False),  # equal to from_version -> exclusive
            ("0.19.0", "0.18.0", "0.19.0", True),  # equal to to_version -> inclusive
            ("0.19.0", "0.19.0", "0.19.0", False),  # from == to == migration
            ("0.19.0", "0.18.0", "0.18.5", False),  # above to_version
            ("0.18.5", "0.18.0", "0.19.0", True),  # strictly inside
            ("0.17.0", "0.18.0", "0.19.0", False),  # below from_version
            ("0.19.0", "0.19.0", "0.19.1", False),  # equal to from_version, wider window
            ("0.19.0", "0.18.0", "0.19.0.dev0", True),  # dev target normalized up
        ],
    )
    def test_range_boundaries(self, discovery, migration_version, from_version, to_version, expected):
        migration = SimpleNamespace(version=Version(migration_version))

        result = discovery._should_include_migration(  # noqa: SLF001
            migration,
            Version(from_version),
            Version(to_version),
        )

        assert result is expected
