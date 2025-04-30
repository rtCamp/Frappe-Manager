import frappe
import sys
import time
import json
import os
from typing import Optional, List, Tuple, Dict, Any
from frappe.utils.background_jobs import get_queues
from rich import print

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
    cleanup: bool = False
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

    Returns:
        A dictionary containing:
            'status': 'success', 'timeout', or 'error'.
            'message': A descriptive message.
            'remaining_jobs': The number of jobs remaining at the end (-1 on error).
    """
    result = {"status": "unknown", "message": "", "remaining_jobs": -1}
    exit_code = 3 # Default to general error
    original_cwd = None

    try:
        # Store original CWD and change to target directory
        original_cwd = os.getcwd()
        target_cwd = "/workspace/frappe-bench/sites"
        if verbose: print(f"[dim]Changing CWD to {target_cwd}...[/dim]", file=sys.stderr)
        os.chdir(target_cwd)

        if verbose: print(f"[dim]Initializing Frappe for site '{site}'...[/dim]", file=sys.stderr)
        frappe.init(site)
        if verbose: print(f"[dim]Connecting to database for site '{site}'...[/dim]", file=sys.stderr)
        frappe.connect()

        start_time = time.time()
        last_job_count = -1

        while True:
            current_time = time.time()
            elapsed_time = current_time - start_time

            if elapsed_time > timeout:
                result = {"status": "timeout", "message": f"Timeout exceeded ({timeout}s)", "remaining_jobs": last_job_count}
                exit_code = 1
                break

            try:
                started_jobs_count, running_job_ids = check_started_jobs(queues_to_monitor=queues)
                last_job_count = started_jobs_count # Store the last known count
            except Exception as e:
                result = {"status": "error", "message": f"Error checking job status: {e}", "remaining_jobs": -1}
                exit_code = 2
                break

            if started_jobs_count == 0:
                result = {"status": "success", "message": "No 'started' jobs found.", "remaining_jobs": 0}
                exit_code = 0
                break
            else:
                # Print running job IDs to stderr
                print(f"\rWaiting... {started_jobs_count} 'started' job(s) remaining. IDs: {running_job_ids}", file=sys.stderr, end="")
                time.sleep(poll_interval)

    except Exception as e:
        # Clear the stderr line on error
        print(file=sys.stderr)
        result = {"status": "error", "message": f"An initialization or other error occurred: {e}", "remaining_jobs": -1}
        exit_code = 3

    finally:
        # Conditionally perform cleanup
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

        # Restore original CWD if it was changed
        if original_cwd:
            if verbose: print(f"[dim]Restoring CWD to {original_cwd}...[/dim]", file=sys.stderr)
            os.chdir(original_cwd)

        return result

