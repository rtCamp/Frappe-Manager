"""
Bench migration state management.

Tracks migration version for individual benches.
"""

from pathlib import Path
from frappe_manager.migration_manager.version import Version
import tomlkit as toml
from datetime import datetime
from typing import Optional


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
    
    try:
        bench_config = toml.parse(bench_config_path.read_text())
        version_str = bench_config.get("migration_state", {}).get("migrated_to")
        
        if version_str:
            return Version(version_str)
    except Exception:
        pass
    
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
    
    toml_doc = toml.parse(bench_config_path.read_text())
    
    if "migration_state" not in toml_doc:
        toml_doc["migration_state"] = toml.table()
    
    toml_doc["migration_state"]["migrated_to"] = str(version.version)
    toml_doc["migration_state"]["last_migration_date"] = datetime.now().isoformat()
    
    with open(bench_config_path, "w") as f:
        f.write(toml.dumps(toml_doc))


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


def get_bench_migration_date(bench_path: Path) -> Optional[str]:
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
    
    try:
        bench_config = toml.parse(bench_config_path.read_text())
        return bench_config.get("migration_state", {}).get("last_migration_date")
    except Exception:
        return None
