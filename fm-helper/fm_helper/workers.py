import frappe
import sys
import time
import json
import os
import frappe
import sys
import time
import json
import os
import contextlib
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from frappe.utils.background_jobs import get_queues
from rich import print

def _get_site_config_key_value(config_path: Path, key_name: str, default: Optional[Any] = None, verbose: bool = False) -> Optional[Any]:
    """Read a specific key's value from a site's config file (site_config.json).

    Args:
        config_path: Path to the site_config.json file.
        key_name: The name of the key to read.
        default: The default value to return if the key is not found or the file is invalid/missing.
        verbose: If True, print status messages.

    Returns:
        The value of the key, or the default value.
    """
    config = {}
    try:
        if config_path.exists():
            with open(config_path, 'r') as f:
                # Suppress error if file is empty or invalid JSON
                with contextlib.suppress(json.JSONDecodeError):
                    config = json.load(f)
            if verbose:
                print(f"[dim]Read config from {config_path}[/dim]", file=sys.stderr)
        elif verbose:
            print(f"[dim]Config file {config_path} does not exist.[/dim]", file=sys.stderr)

        value = config.get(key_name, default)
        if verbose:
            print(f"[dim]Value for key '{key_name}': {json.dumps(value)}[/dim]", file=sys.stderr)
        return value

    except OSError as e:
        if verbose:
            print(f"[yellow]Warning:[/yellow] Could not read {config_path}: {e}", file=sys.stderr)
        # In case of read error, return default, as we can't determine the value
        return default


def _update_site_config_key(config_path: Path, key_name: str, value: Optional[Any], verbose: bool = False) -> None:
    """Update a specific key in a site's config file (site_config.json).

    Args:
        config_path: Path to the site_config.json file.
        key_name: The name of the key to update (e.g., "pause_scheduler", "maintenance_mode").
        value: The value to set for the key. If None, the key will be removed.
        verbose: If True, print status messages.
    """
    try:
        # Read current config safely
        config = {}
        if config_path.exists():
            with open(config_path, 'r') as f:
                # Suppress error if file is empty or invalid JSON, start with empty config
                with contextlib.suppress(json.JSONDecodeError):
                    config = json.load(f)

        needs_update = False
        current_value = config.get(key_name)

        # Determine if update is needed
        if value is None:
            if key_name in config:
                del config[key_name]  # Remove the key if value is None and key exists
                needs_update = True
        elif current_value != value:
            config[key_name] = value # Set or update the key
            needs_update = True

        if needs_update:
            # Write back
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=1) # Use indent=1 for consistency

            if verbose:
                action = "Removed" if value is None else f"Set to {json.dumps(value)}" # Use json.dumps for representation
                print(f"[dim]Updated {config_path}: '{key_name}' {action}[/dim]", file=sys.stderr)
        elif verbose:
             print(f"[dim]'{key_name}' already set appropriately in {config_path}. No change needed.[/dim]", file=sys.stderr)

    except (OSError, json.JSONDecodeError, Exception) as e:
        if verbose:
            print(f"[yellow]Warning:[/yellow] Failed to update key '{key_name}' in {config_path}: {e}",
                  file=sys.stderr)
        # Re-raise the exception so the caller knows it failed
        raise

def check_started_jobs(queues_to_monitor: Optional[List[str]] = None) -> Tuple[int, List[str]]:
    """
    Counts the number of jobs currently in the 'started' state.

    Parameters:
        queues_to_monitor (Optional[List[str]]): A list of queue names to check.
                                                 If None or empty, checks all queues.

    Returns:
        Tuple[int, List[str]]: A tuple containing the total count of 'started' jobs
                               and a list of their IDs.
    """
    started_job_count = 0
    started_job_ids = []
    all_queues = get_queues()

    if queues_to_monitor:
        # Filter queues based on the provided list
        target_queues = [q for q in all_queues if q.name in queues_to_monitor]
        if len(target_queues) != len(queues_to_monitor):
            # Warn if some specified queues don't exist
            found_names = {q.name for q in target_queues}
            missing_queues = [q_name for q_name in queues_to_monitor if q_name not in found_names]
            print(f"Warning: Specified queues not found: {', '.join(missing_queues)}", file=sys.stderr)
    else:
        # Monitor all queues if none are specified
        target_queues = all_queues

    for queue in target_queues:
        try:
            # Fetch only 'started' jobs for the queue using the registry
            job_ids = queue.started_job_registry.get_job_ids()
            started_job_count += len(job_ids)
            started_job_ids.extend(job_ids)
        except Exception as e:
            # Handle potential errors during fetch (e.g., Redis connection issues)
            print(f"Warning: Could not fetch jobs for queue '{queue.name}': {e}", file=sys.stderr)

    return started_job_count, started_job_ids


# Removed typer app initialization and command decorator

def wait_for_jobs_to_finish(
    site: str,
    timeout: int,
    poll_interval: int,
    queues: Optional[List[str]] = None,
    verbose: bool = False,
    cleanup: bool = False,
    pause_scheduler_during_wait: bool = False
) -> Dict[str, Any]:
    """
    Waits for currently 'started' Frappe background jobs to finish.

    Connects to the specified site and monitors job queues.

    Args:
        site: The Frappe site name.
        queues: Specific queue names to monitor (monitors all if None).
        timeout: Maximum time in seconds to wait.
        poll_interval: Interval in seconds between checks.
        verbose: If True, print progress messages to stderr.
        cleanup: If True, explicitly close DB and Redis connections (default: False).
        pause_scheduler_during_wait: If True, pause the scheduler during wait and restore after.

    Returns:
        A dictionary containing:
            'status': 'success', 'timeout', or 'error'.
            'message': A descriptive message.
            'remaining_jobs': The number of jobs remaining at the end (-1 on error).
            'original_scheduler_state': The value of 'pause_scheduler' before modification (None, 0, or 1).
            'scheduler_was_paused': Boolean indicating if this function paused the scheduler.
    """
    # Initialize result with new keys
    result = {
        "status": "unknown",
        "message": "",
        "remaining_jobs": -1,
        "original_scheduler_state": None,
        "scheduler_was_paused": False
    }
    exit_code = 3 # Default to general error
    original_cwd = None # Initialize original_cwd before try block
    
    # Handle scheduler pausing if requested
    site_config_path = Path(f"/workspace/frappe-bench/sites/{site}/site_config.json")
    
    if pause_scheduler_during_wait:
        try:
            # Read current scheduler state using the new helper
            result["original_scheduler_state"] = _get_site_config_key_value(
                site_config_path, "pause_scheduler", default=None, verbose=verbose
            )
            
            # Pause the scheduler only if it's not already paused
            if result["original_scheduler_state"] != 1:
                _update_site_config_key(site_config_path, key_name="pause_scheduler", value=1, verbose=verbose)
                result["scheduler_was_paused"] = True
            elif verbose:
                 print(f"[dim]'pause_scheduler' already set to 1. No change needed.[/dim]", file=sys.stderr)

        except Exception as e: # Catch potential errors from _update_site_config_key as well
            if verbose:
                print(f"[yellow]Warning:[/yellow] Failed to read or pause scheduler: {e}", file=sys.stderr)

    try:
        target_cwd = Path("/workspace/frappe-bench/sites") # Define target CWD
        original_cwd = Path.cwd() # Store original CWD *before* changing

        if verbose: print(f"[dim]Changing CWD to {target_cwd}...[/dim]", file=sys.stderr)
        os.chdir(target_cwd)

        if verbose: print(f"[dim]Initializing Frappe for site '{site}'...[/dim]", file=sys.stderr)
        # Pass sites_path explicitly during init when CWD is changed
        frappe.init(site=site, sites_path=str(target_cwd))
        if verbose: print(f"[dim]Connecting to database for site '{site}'...[/dim]", file=sys.stderr)
        frappe.connect()

        start_time = time.time()
        last_job_count = -1

        while True:
            current_time = time.time()
            elapsed_time = current_time - start_time

            if elapsed_time > timeout:
                result.update({"status": "timeout", "message": f"Timeout exceeded ({timeout}s)", "remaining_jobs": last_job_count})
                exit_code = 1
                break

            try:
                started_jobs_count, running_job_ids = check_started_jobs(queues_to_monitor=queues)
                last_job_count = started_jobs_count # Store the last known count
            except Exception as e:
                result.update({"status": "error", "message": f"Error checking job status: {e}", "remaining_jobs": -1})
                exit_code = 2
                break

            if started_jobs_count == 0:
                result.update({"status": "success", "message": "No 'started' jobs found.", "remaining_jobs": 0})
                exit_code = 0
                break
            else:
                # Print running job IDs to stderr
                print(f"\rWaiting... {started_jobs_count} 'started' job(s) remaining. IDs: {running_job_ids}", file=sys.stderr, end="\n")
                time.sleep(poll_interval)

    except Exception as e:
        # Clear the stderr line on error
        print(file=sys.stderr)
        result.update({"status": "error", "message": f"An initialization or other error occurred: {e}", "remaining_jobs": -1})
        exit_code = 3

    finally:
        if cleanup:
            if frappe.local.db:
                frappe.db.close()
                if verbose: print("\n[dim]Database connection closed.[/dim]", file=sys.stderr)

            try:
                if frappe.local.conf and frappe.local.conf.get('redis_cache'):
                    from frappe.utils.redis_wrapper import RedisWrapper
                    RedisWrapper.close_all()
                    if verbose: print("[dim]Attempted to close Redis connections.[/dim]", file=sys.stderr)
            except Exception as cleanup_err:
                if verbose:
                    print(f"\n[yellow]Warning:[/yellow] Error during Redis cleanup: {cleanup_err}", file=sys.stderr)

        # frappe.destroy() # Avoid destroy for cleaner exit

        # Restore original CWD if it was changed and is different
        if original_cwd and Path.cwd() != original_cwd:
            if verbose: print(f"[dim]Restoring CWD to {original_cwd}...[/dim]", file=sys.stderr)
            os.chdir(original_cwd)

        # Ensure final newline after potential \r updates from the loop
        print(file=sys.stderr)
        return result

