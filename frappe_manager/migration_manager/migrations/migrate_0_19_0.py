"""
Migration for v0.19.0

This migration represents the start of the rolling migration window.
v0.19.0 supports migrations from v0.18.0 and later.

Rolling Migration Policy:
- Each version contains migrations from the previous major API version
- When API breaks occur, old migrations are removed
- Users must upgrade to the last version before an API break before upgrading further

Example timeline:
- v19: Supports v18 → v19
- v20, v21: Supports v19 → v20 → v21 (cumulative)
- v22 (API break): Remove all migrations, start fresh with v22
"""

from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.version import Version
from frappe_manager.migration_manager.migration_helpers import MigrationBench


class MigrationV0190(MigrationBase):
    version = Version("0.19.0")

    def migrate_bench(self, bench: MigrationBench):
        """
        Migrate individual bench from v0.18.0 to v0.19.0.

        Add your migration logic here when needed.
        Currently a no-op placeholder.
        """
        pass

    def undo_bench_migrate(self, bench: MigrationBench):
        """
        Rollback bench migration from v0.19.0 to v0.18.0.

        Add your rollback logic here when needed.
        """
        pass

    def migrate_services(self):
        """
        Migrate global services configuration.

        Add your service migration logic here when needed.
        """
        pass

    def undo_services_migrate(self):
        """
        Rollback services migration.

        Add your rollback logic here when needed.
        """
        pass
