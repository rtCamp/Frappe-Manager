import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

COMMON_SITE_CONFIG_PATH = Path("/workspace/frappe-bench/sites/common_site_config.json")


def read_common_site_config(verbose: bool = False) -> dict:
    """Read and return common_site_config.json as a dict.

    Args:
        verbose: If True, print status messages to stderr.

    Returns:
        Parsed config dict, or {} on missing/invalid file.
    """
    config = {}
    try:
        if COMMON_SITE_CONFIG_PATH.exists():
            with open(COMMON_SITE_CONFIG_PATH, 'r') as f:
                with contextlib.suppress(json.JSONDecodeError):
                    config = json.load(f)
            if verbose:
                print(f"[dim]Read config from {COMMON_SITE_CONFIG_PATH}[/dim]", file=sys.stderr)
        elif verbose:
            print(f"[dim]Config file {COMMON_SITE_CONFIG_PATH} does not exist.[/dim]", file=sys.stderr)
    except OSError as e:
        if verbose:
            print(f"[yellow]Warning:[/yellow] Could not read {COMMON_SITE_CONFIG_PATH}: {e}", file=sys.stderr)
    return config


def get_common_site_config_value(key: str, default: Optional[Any] = None, verbose: bool = False) -> Optional[Any]:
    """Read a specific key's value from common_site_config.json.

    Args:
        key: The name of the key to read.
        default: Value to return if the key is absent or the file is invalid/missing.
        verbose: If True, print status messages to stderr.

    Returns:
        The value associated with ``key``, or ``default`` if not found.
    """
    config = read_common_site_config(verbose=verbose)
    value = config.get(key, default)
    if verbose:
        print(f"[dim]Value for key '{key}': {json.dumps(value)}[/dim]", file=sys.stderr)
    return value


def set_common_site_config_value(
    key: str,
    value: Any,
    verbose: bool = False,
    display=None,
) -> bool:
    """Write a single key-value pair into common_site_config.json, preserving all existing keys.

    Args:
        key: The config key to set.
        value: The value to assign.
        verbose: If True, emit status messages via ``display`` when provided.
        display: Optional DisplayManager for verbose output.

    Returns:
        True on success, False if the file could not be written.
    """
    config = read_common_site_config()
    try:
        config[key] = value
        with open(COMMON_SITE_CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=4)
        if verbose and display:
            display.info(f"Set {key} to {value} in {COMMON_SITE_CONFIG_PATH}")
        return True
    except Exception as e:
        if verbose and display:
            display.warning(f"Could not write {key} to {COMMON_SITE_CONFIG_PATH}: {e}")
        return False
