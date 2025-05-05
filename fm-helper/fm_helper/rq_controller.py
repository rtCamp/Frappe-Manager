import sys
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from enum import Enum
from rich import print

# --- Try importing RQ components ---
try:
    from rq.suspension import suspend, resume, is_suspended
    import redis
    from .workers import _get_site_config_key_value
    module_import_error = None
except ImportError as e:
    redis = None
    suspend = None
    resume = None
    is_suspended = None
    module_import_error = e
# --- End Imports ---

# Define Action Enum
class ActionEnum(str, Enum):
    suspend = "suspend"
    resume = "resume"

def control_rq_workers(action: ActionEnum) -> bool:
    """
    Connects to Redis using common_site_config.json and performs RQ suspension actions.

    Args:
        action: Action to perform (suspend/resume)

    Returns:
        bool: True on success, False on failure
    """
    if module_import_error:
        print(f"[bold red]Error:[/bold red] Failed to import Redis/RQ modules: {module_import_error}", file=sys.stderr)
        print("Ensure Redis and RQ are installed in the environment.", file=sys.stderr)
        return False

    try:
        # Get Redis URL from common config
        redis_url = _get_site_config_key_value("redis_queue", verbose=True)
        if not redis_url:
            print("[bold red]Error:[/bold red] 'redis_queue' URL not found in common_site_config.json.", file=sys.stderr)
            return False

        # Create Redis connection
        try:
            print(f"[dim]Connecting to Redis via URL: {redis_url}...[/dim]", file=sys.stderr)
            connection = redis.from_url(redis_url)
            # Test connection
            connection.ping()
            print("[dim]Redis connection successful.[/dim]", file=sys.stderr)
        except redis.exceptions.ConnectionError as conn_err:
            print(f"[bold red]Error:[/bold red] Failed to connect to Redis at '{redis_url}': {conn_err}", file=sys.stderr)
            return False
        except ValueError as url_err:
            print(f"[bold red]Error:[/bold red] Invalid Redis URL format found in common_site_config.json: '{redis_url}' - {url_err}", file=sys.stderr)
            return False

        if action == ActionEnum.suspend:
            print(f"[cyan]Suspending RQ workers via Redis flag...[/cyan]", file=sys.stderr)
            suspend(connection)
            print(f"[green]Successfully set suspension flag in Redis.[/green]", file=sys.stderr)
            return True
        elif action == ActionEnum.resume:
            print(f"[cyan]Resuming RQ workers via Redis flag...[/cyan]", file=sys.stderr)
            print('TODO: Not resuming')

            # result = resume(connection) # Returns number of keys deleted (1 or 0)
            if result == 1:
                print(f"[green]Successfully removed suspension flag in Redis.[/green]", file=sys.stderr)
            else:
                print(f"[dim]Suspension flag was not present in Redis.[/dim]", file=sys.stderr)
            return True

    except Exception as e:
        print(f"\n[bold red]Error:[/bold red] Failed during RQ {action.value}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return False

    finally:
        pass

def check_rq_suspension() -> Optional[bool]:
    """
    Connects to Redis using common_site_config.json and checks if RQ workers are suspended.

    Returns:
        Optional[bool]: True if suspended, False if not suspended, None on error.
    """
    if module_import_error:
        print(f"[bold red]Error (check_rq_suspension):[/bold red] Failed to import Redis/RQ modules: {module_import_error}", file=sys.stderr)
        return None

    try:
        # Get Redis URL from common config
        redis_url = _get_site_config_key_value("redis_queue", verbose=False)
        if not redis_url:
            print("[bold red]Error:[/bold red] 'redis_queue' URL not found in common_site_config.json.", file=sys.stderr)
            return None

        # Create Redis connection
        try:
            connection = redis.from_url(redis_url)
            connection.ping()  # Test connection
        except (redis.exceptions.ConnectionError, ValueError) as e:
            print(f"[bold red]Error:[/bold red] Failed to connect to Redis: {e}", file=sys.stderr)
            return None

        suspended = is_suspended(connection)
        return bool(suspended)  # Convert Redis result (0 or 1) to boolean

    except Exception as e:
        print(f"\n[bold red]Error (check_rq_suspension):[/bold red] Failed during RQ suspension check: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return None # Indicate error

    finally:
        # No cleanup needed
        pass
