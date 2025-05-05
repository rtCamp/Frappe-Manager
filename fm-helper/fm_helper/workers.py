import sys
import json
import os
import contextlib
from pathlib import Path
from typing import Optional, Any
from rich import print

def _get_site_config_key_value(key_name: str, default: Optional[Any] = None, verbose: bool = False) -> Optional[Any]:
    """Read a specific key's value from common_site_config.json.

    Args:
        key_name: The name of the key to read.
        default: The default value to return if the key is not found or the file is invalid/missing.
        verbose: If True, print status messages.

    Returns:
        The value of the key, or the default value.
    """
    common_config_path = Path("/workspace/frappe-bench/sites/common_site_config.json")
    config = {}
    try:
        if common_config_path.exists():
            with open(common_config_path, 'r') as f:
                # Suppress error if file is empty or invalid JSON
                with contextlib.suppress(json.JSONDecodeError):
                    config = json.load(f)
            if verbose:
                print(f"[dim]Read config from {common_config_path}[/dim]", file=sys.stderr)
        elif verbose:
            print(f"[dim]Config file {common_config_path} does not exist.[/dim]", file=sys.stderr)

        value = config.get(key_name, default)
        if verbose:
            print(f"[dim]Value for key '{key_name}': {json.dumps(value)}[/dim]", file=sys.stderr)
        return value

    except OSError as e:
        if verbose:
            print(f"[yellow]Warning:[/yellow] Could not read {common_config_path}: {e}", file=sys.stderr)
        # In case of read error, return default, as we can't determine the value
        return default

