"""
Migration validation and filtering logic.
"""

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.migration_manager.bench_migration_state import get_bench_migration_version
from frappe_manager.migration_manager.migration_constants import MINIMUM_SUPPORTED_VERSION
from frappe_manager.migration_manager.migration_helpers import MigrationBenches
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager import OutputHandler


class BenchFilter:
    """
    Handles bench filtering logic based on target_benches and exclude_benches.

    Single source of truth for "should this bench be processed?" logic.
    """

    def __init__(
        self,
        target_benches: list[str] | None,
        exclude_benches: list[str],
    ):
        self.target_benches = target_benches
        self.exclude_benches = exclude_benches

    def should_process_bench(self, bench_name: str) -> bool:
        """
        Determine if a bench should be processed during migration.

        Returns False if:
        - target_benches is None (infrastructure-only migration)
        - bench_name not in target_benches (when targeting specific benches)
        - bench_name in exclude_benches (explicitly excluded)
        """
        if self.target_benches is None:
            return False

        if bench_name not in self.target_benches:
            return False

        if bench_name in self.exclude_benches:
            return False

        return True


class MigrationValidator:
    """
    Validates migration preconditions and checks version compatibility.
    """

    def __init__(
        self,
        prev_version: Version,
        current_version: Version,
        bench_filter: BenchFilter,
        output_handler: OutputHandler,
    ):
        self.prev_version = prev_version
        self.current_version = current_version
        self.bench_filter = bench_filter
        self.output = output_handler

    def get_minimum_bench_version(self) -> Version:
        """
        Get the minimum migration version across all target benches.

        Returns the lowest version that needs migration. This determines
        which migration classes need to be loaded.
        """
        if self.bench_filter.target_benches is None:
            return self.current_version

        benches_manager = MigrationBenches(CLI_BENCHES_DIRECTORY)
        all_benches = benches_manager.get_all_benches()
        min_version = self.current_version

        for bench_name, bench_path in all_benches.items():
            if not self.bench_filter.should_process_bench(bench_name):
                continue

            bench_version = get_bench_migration_version(bench_path.parent)

            min_version = min(min_version, bench_version)

        return min_version

    def check_benches_need_migration(self) -> bool:
        """Check if any target benches need migration to current version."""
        if self.bench_filter.target_benches is None:
            return False

        benches_manager = MigrationBenches(CLI_BENCHES_DIRECTORY)
        all_benches = benches_manager.get_all_benches()

        for bench_name, bench_path in all_benches.items():
            if not self.bench_filter.should_process_bench(bench_name):
                continue

            bench_version = get_bench_migration_version(bench_path.parent)
            if bench_version < self.current_version:
                return True

        return False

    def validate_version_support(self, effective_prev_version: Version) -> bool:
        """
        Check if migration from effective_prev_version is supported.

        Returns False and displays error if version is too old.
        """
        if effective_prev_version == Version("0.0.0"):
            return True

        if effective_prev_version < MINIMUM_SUPPORTED_VERSION:
            self.output.display_error(
                f"Cannot migrate from v{effective_prev_version.version}. "
                f"Minimum supported version is v{MINIMUM_SUPPORTED_VERSION.version}.",
            )
            self.output.display_error(
                f"\nPlease upgrade to v{MINIMUM_SUPPORTED_VERSION.version} first, "
                f"then upgrade to v{self.current_version.version}.",
            )
            self.output.display_error(
                f"\nMigration path: v{effective_prev_version.version} → "
                f"v{MINIMUM_SUPPORTED_VERSION.version} → v{self.current_version.version}",
            )
            return False

        return True
