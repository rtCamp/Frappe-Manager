"""
Bench migration state management.

Tracks migration version for individual benches.
"""

from datetime import datetime
from pathlib import Path

import tomlkit

from frappe_manager import CLI_BENCH_CONFIG_FILE_NAME
from frappe_manager.migration_manager.version import Version
from frappe_manager.site_manager.bench_config import BenchConfig, SchemaState, _recorded_schema_version


def _read_schema_table(bench_config_path: Path) -> dict:
    """Raw-TOML read of ``[schema]``, tolerating a config the model would reject.

    Deliberately NOT via the BenchConfig model: the version probe runs before
    every command, and a config that fails schema validation must still report
    its real version -- otherwise validation errors get masked as a bogus
    "migration required (v0.0.0)" prompt. The actual command's config load
    surfaces the real error.

    ``[migration_state]`` is the table's pre-1.0.0 spelling, and every bench written before the
    rename carries it. Reading one as 0.0.0 would make the gate REFUSE it as an unknown version
    instead of migrating it, which is a hard stop on precisely the benches this release upgrades.
    """
    try:
        data = tomlkit.parse(bench_config_path.read_text())
    except Exception:
        return {}
    state = data.get("schema") or data.get("migration_state")
    return dict(state) if isinstance(state, dict) else {}


def get_bench_migration_version(bench_path: Path) -> Version:
    """
    Get the version bench is migrated to.

    Args:
        bench_path: Path to bench directory

    Returns:
        Version object representing the bench's recorded schema version
    """
    bench_config_path = bench_path / CLI_BENCH_CONFIG_FILE_NAME

    if not bench_config_path.exists():
        return Version("0.0.0")

    recorded = _recorded_schema_version(_read_schema_table(bench_config_path))
    if recorded:
        return Version(str(recorded))

    return Version("0.0.0")


def set_bench_migration_version(bench_path: Path, version: Version) -> None:
    """
    Update bench migration version.

    Args:
        bench_path: Path to bench directory
        version: Version to set
    """
    bench_config_path = bench_path / CLI_BENCH_CONFIG_FILE_NAME

    if not bench_config_path.exists():
        raise FileNotFoundError(f"Bench config not found: {bench_config_path}")

    config = BenchConfig.import_from_toml(bench_config_path)
    version_str = str(version.version)
    last_migration_date = datetime.now().isoformat()
    if config.schema_state is not None:
        # Mutate the loaded instance, don't rebuild it: SchemaState is extra="allow", so a stray
        # key in [schema] only survives if it stays on the SAME instance import_from_toml
        # returned. A fresh SchemaState(version=..., last_migration_date=...) would construct
        # without the stray kwarg and silently drop it on every migration.
        config.schema_state.version = version_str
        config.schema_state.last_migration_date = last_migration_date
    else:
        config.schema_state = SchemaState(
            version=version_str,
            last_migration_date=last_migration_date,
        )
    config.export_to_toml(bench_config_path)


def bench_needs_migration(bench_path: Path, target_version: Version) -> bool:
    """
    Check if bench needs migration to target version.

    Args:
        bench_path: Path to bench directory
        target_version: Target migration version

    Returns:
        True if bench needs migration, False otherwise
    """
    current = get_bench_migration_version(bench_path)
    return current < target_version

