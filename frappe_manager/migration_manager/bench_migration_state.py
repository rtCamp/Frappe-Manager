"""
Bench migration state management.

Tracks migration version for individual benches.
"""

from datetime import datetime
from pathlib import Path

import tomlkit

from frappe_manager.migration_manager.version import Version
from frappe_manager.site_manager.bench_config import BenchConfig, MigrationState


def _read_migration_state(bench_config_path: Path) -> dict:
    """Raw-TOML read of ``[migration_state]`` (schema-tolerant).

    Deliberately NOT via the BenchConfig model: the version probe runs before
    every command, and a config that fails schema validation must still report
    its real version -- otherwise validation errors get masked as a bogus
    "migration required (v0.0.0)" prompt. The actual command's config load
    surfaces the real error.
    """
    try:
        data = tomlkit.parse(bench_config_path.read_text())
    except Exception:
        return {}
    state = data.get("migration_state")
    return dict(state) if isinstance(state, dict) else {}


def get_bench_migration_version(bench_path: Path) -> Version:
    """
    Get the version bench is migrated to.

    Args:
        bench_path: Path to bench directory

    Returns:
        Version object representing bench migration state
    """
    bench_config_path = bench_path / "bench_config.toml"

    if not bench_config_path.exists():
        return Version("0.0.0")

    migrated_to = _read_migration_state(bench_config_path).get("migrated_to")
    if migrated_to:
        return Version(str(migrated_to))

    return Version("0.0.0")


def set_bench_migration_version(bench_path: Path, version: Version) -> None:
    """
    Update bench migration version.

    Args:
        bench_path: Path to bench directory
        version: Version to set
    """
    bench_config_path = bench_path / "bench_config.toml"

    if not bench_config_path.exists():
        raise FileNotFoundError(f"Bench config not found: {bench_config_path}")

    config = BenchConfig.import_from_toml(bench_config_path)
    config.migration_state = MigrationState(
        migrated_to=str(version.version),
        last_migration_date=datetime.now().isoformat(),
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


def get_bench_migration_date(bench_path: Path) -> str | None:
    """
    Get last migration date for bench.

    Args:
        bench_path: Path to bench directory

    Returns:
        ISO format date string or None
    """
    bench_config_path = bench_path / "bench_config.toml"

    if not bench_config_path.exists():
        return None

    return _read_migration_state(bench_config_path).get("last_migration_date")
