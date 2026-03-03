"""
Migration discovery - dynamically loads migration classes from migrations/ directory.
"""

import importlib
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

from frappe_manager.logger import log
from frappe_manager.migration_manager.version import Version
from frappe_manager.utils.helpers import capture_and_format_exception

if TYPE_CHECKING:
    from frappe_manager.migration_manager.migration_base import MigrationBase
    from frappe_manager.output_manager import OutputHandler


class MigrationDiscovery:
    """
    Discovers and loads migration classes from the migrations/ directory.

    Handles dynamic import and validation of migration modules.
    """

    def __init__(self, migrations_path: Path, output_handler: "OutputHandler"):
        self.migrations_path = migrations_path
        self.output = output_handler
        self.logger = log.get_logger()

    def discover_migrations(
        self,
        from_version: Version,
        to_version: Version,
        migration_executor: "object",
    ) -> list["MigrationBase"]:
        """
        Discover and load migrations needed to go from from_version to to_version.

        Args:
            from_version: Starting version
            to_version: Target version
            migration_executor: MigrationExecutor instance to inject into migrations

        Returns:
            Sorted list of migration instances to execute
        """
        migrations = []

        for _, module_name, _ in pkgutil.iter_modules([str(self.migrations_path)]):
            try:
                migration_class = self._load_migration_class(module_name)
                if migration_class:
                    migration_instance = self._instantiate_migration(migration_class, migration_executor)

                    if self._should_include_migration(migration_instance, from_version, to_version):
                        migrations.append(migration_instance)

            except Exception:
                exception_str = capture_and_format_exception()
                self.logger.error(f"Failed to register migration {module_name}: {exception_str}")
                self.output.warning(
                    f"Skipping migration module '{module_name}' due to load failure. Check logs for details.",
                )

        return sorted(migrations, key=lambda m: m.version)

    def _load_migration_class(self, module_name: str) -> type | None:
        """
        Load a migration class from a module.

        Returns None if module doesn't contain a valid migration class.
        """
        module = importlib.import_module(f".migrations.{module_name}", "frappe_manager.migration_manager")

        for attr_name in dir(module):
            attr = getattr(module, attr_name)

            if self._is_valid_migration_class(attr):
                if attr.version != Version("0.0.0"):
                    return attr

        return None

    def _is_valid_migration_class(self, obj: object) -> bool:
        """Check if object is a valid migration class."""
        return (
            isinstance(obj, type)
            and hasattr(obj, "up")
            and hasattr(obj, "down")
            and hasattr(obj, "set_migration_executor")
            and hasattr(obj, "version")
        )

    def _instantiate_migration(
        self,
        migration_class: type,
        migration_executor: "object",
    ) -> "MigrationBase":
        """Create migration instance and inject executor."""
        migration = migration_class(output_handler=self.output)
        migration.set_migration_executor(migration_executor=migration_executor)
        return migration

    def _should_include_migration(
        self,
        migration: "MigrationBase",
        from_version: Version,
        to_version: Version,
    ) -> bool:
        """
        Check if migration version is in the range we need to execute.

        Special handling for dev versions: If to_version is a dev/pre-release version
        (e.g., 0.19.0.dev0), we normalize it to the base release version (0.19.0) for
        comparison. This ensures migrations for the target release are included during
        development cycles.

        In PEP 440, dev versions come BEFORE their release: 0.19.0.dev0 < 0.19.0
        Without normalization, migration 0.19.0 would be excluded when running 0.19.0.dev0.
        """
        # Normalize to_version to base version (strips .devN, .a, .b, .rc suffixes)
        # This allows migrations for release X.Y.Z to run during X.Y.Z.devN development
        # Access base_version through internal _parsed PackagingVersion object
        normalized_to = Version(to_version._parsed.base_version)
        return from_version < migration.version <= normalized_to
