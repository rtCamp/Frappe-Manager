import frappe
import sys
import time
import json
import argparse
from typing import Optional, List, Tuple
from frappe.utils.background_jobs import get_queues

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

def main(site: str, queue: Optional[List[str]], timeout: int, poll_interval: int):
    """
    Waits for currently 'started' Frappe background jobs to finish and outputs JSON status.

    Exit Codes:
        0: Success (no started jobs remaining)
        1: Timeout
        2: Error checking job status
        3: Initialization or other error
    """
    result = {"status": "unknown", "message": "", "remaining_jobs": -1}
    exit_code = 3 # Default to general error

    try:
        frappe.init(site)
        if not frappe.conf.db_name:
             raise Exception(f"Database name not found for site {site}. Check site_config.json.")
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
                started_jobs_count, running_job_ids = check_started_jobs(queues_to_monitor=queue)
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
        if frappe.local.db:
            frappe.db.close()
        if frappe.local.conf and frappe.local.conf.get('redis_cache'):
             # Attempt to close redis connection if exists
             # Attempt to close redis connection if exists (best effort)
             try:
                 if frappe.local.conf and frappe.local.conf.get('redis_cache'):
                     from frappe.utils.redis_wrapper import RedisWrapper
                     RedisWrapper.close_all()
             except Exception:
                 pass # Ignore errors during cleanup
        # frappe.destroy() # Avoid destroy for cleaner exit

        # Output final result as JSON to stdout
        print(json.dumps(result))
        sys.exit(exit_code)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Waits for currently 'started' Frappe background jobs to finish.")
    parser.add_argument("site", help="The Frappe site name to connect to.")
    parser.add_argument("-q", "--queue", action="append", help="Specific queue(s) to monitor. Monitors all if not specified.")
    parser.add_argument("-t", "--timeout", type=int, default=300, help="Maximum time in seconds to wait for jobs to finish (default: 300).")
    parser.add_argument("-i", "--poll-interval", type=int, default=5, help="Interval in seconds between checking job status (default: 5).")

    args = parser.parse_args()

    main(site=args.site, queue=args.queue, timeout=args.timeout, poll_interval=args.poll_interval)
