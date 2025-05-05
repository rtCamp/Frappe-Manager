import sys
import os
import time
import traceback
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
from enum import Enum
from rich import print
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.console import Group


# --- Try importing RQ components ---

# --- No-operation function for RQ ---
def noop():
    """Does absolutely nothing. Used to wake up idle RQ workers."""
    pass
# --- End No-operation function ---
try:
    from rq.suspension import suspend, resume, is_suspended
    from rq.worker import Worker
    from rq import Queue
    import redis
    from .workers import _get_site_config_key_value
    module_import_error = None
except ImportError as e:
    redis = None
    suspend = None
    resume = None
    is_suspended = None
    Worker = None
    Queue = None
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

            # --- Enqueue noop job ---
            try:
                if Queue is None: # Check if Queue import failed
                     print("[yellow]Warning:[/yellow] Cannot enqueue noop job because RQ Queue class failed to import.", file=sys.stderr)
                else:
                    print("[dim]Enqueuing noop jobs to wake idle workers...[/dim]", file=sys.stderr)
                    queues = Queue.all(connection=connection)
                    if queues:
                        noop_job_count = 0
                        for queue in queues:
                            try:
                                # Enqueue using the full import path string
                                queue.enqueue('fm_helper.rq_controller.noop',at_front=True)
                                noop_job_count += 1
                            except Exception as enqueue_err:
                                print(f"[yellow]Warning:[/yellow] Failed to enqueue noop job to queue '{queue.name}': {enqueue_err}", file=sys.stderr)
                        if noop_job_count > 0:
                            print(f"[dim]Enqueued noop job to {noop_job_count} queue(s).[/dim]", file=sys.stderr)
                        else:
                            print("[dim]No queues found or noop job could not be enqueued.[/dim]", file=sys.stderr)
                    else:
                        print("[dim]No RQ queues found to enqueue noop job.[/dim]", file=sys.stderr)
            except Exception as e:
                print(f"[yellow]Warning:[/yellow] Error occurred during noop job enqueue: {e}", file=sys.stderr)
                # Do not return False here, continue with the suspend success
            # --- End Enqueue noop job ---

            return True
        elif action == ActionEnum.resume:
            print(f"[cyan]Resuming RQ workers via Redis flag...[/cyan]", file=sys.stderr)
            result = resume(connection) # Returns number of keys deleted (1 or 0)
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

def wait_for_rq_workers_suspended(timeout: int = 300, poll_interval: int = 5, verbose: bool = False) -> bool:
    """
    Wait for all RQ workers registered in Redis to reach the 'suspended' state.
    Does not require site initialization, works directly with Redis.

    Args:
        timeout: Maximum time to wait in seconds.
        poll_interval: Time between checks in seconds.
        verbose: If True, print the state of each worker during checks.

    Returns:
        bool: True if all workers are suspended/gone, False on timeout or error.
    """
    if module_import_error:
        print(f"[bold red]Error:[/bold red] Failed to import Redis/RQ modules: {module_import_error}", file=sys.stderr)
        return False

    try:
        # Get Redis URL from common config
        redis_url = _get_site_config_key_value("redis_queue", verbose=False)
        if not redis_url:
            print("[bold red]Error:[/bold red] 'redis_queue' URL not found in common_site_config.json.", file=sys.stderr)
            return False

        try:
            connection = redis.from_url(redis_url)
            connection.ping()  # Test connection
        except (redis.exceptions.ConnectionError, ValueError) as e:
            print(f"[bold red]Error:[/bold red] Failed to connect to Redis: {e}", file=sys.stderr)
            return False

        print(f"Waiting up to {timeout}s for RQ workers to reach 'suspended' state...")
        start_time = time.time()
        final_status = "unknown"  # Track loop exit reason

        with Live(refresh_per_second=4, transient=True, vertical_overflow="visible") as live:
            while True:
                current_time = time.time()
                elapsed_time = current_time - start_time

                if elapsed_time > timeout:
                    final_status = "timeout"
                    break

                try:
                    # --- Get Worker Data ---
                    workers = Worker.all(connection=connection)
                    if not workers:
                        final_status = "no_workers"
                        break

                    non_suspended_workers = []
                    worker_states = []  # Store tuples of (name, state) for table
                    for worker in workers:
                        state = worker.state
                        worker_states.append((worker.name, state))
                        if state != 'suspended':
                            non_suspended_workers.append(worker.name)

                    if not non_suspended_workers:
                        final_status = "success"
                        break

                    # --- Build Renderables ---
                    # 1. Summary Panel
                    remaining_time = max(0, timeout - elapsed_time)
                    summary_text = (
                        f"Elapsed: [cyan]{elapsed_time:.1f}s[/] | Remaining: [cyan]{remaining_time:.1f}s[/]\n"
                        f"Waiting for [yellow]{len(non_suspended_workers)}[/] worker(s) to suspend: {', '.join(non_suspended_workers)}"
                    )
                    summary_panel = Panel(summary_text, title="RQ Worker Wait Status", border_style="blue", expand=False)

                    # 2. Worker Table (Verbose Only)
                    renderables = [summary_panel]
                    if verbose:
                        worker_table = Table(title="Worker States", expand=False)
                        worker_table.add_column("Worker Name", style="dim")
                        worker_table.add_column("State")

                        for name, state in worker_states:
                            state_color = "green" if state == 'suspended' else "yellow" if state in ('idle', 'busy') else "red"
                            worker_table.add_row(name, f"[{state_color}]{state}[/{state_color}]")
                        renderables.append(worker_table)

                    # 3. Group them
                    render_group = Group(*renderables)

                    # 4. Update Live display
                    live.update(render_group)

                    time.sleep(poll_interval)

                except redis.RedisError as e:
                    print(f"\n[bold red]Error:[/bold red] Redis operation failed while checking workers: {e}", file=sys.stderr)
                    return False
                except Exception as e:  # Catch potential errors from Worker.all() or worker.state
                    print(f"\n[bold red]Error:[/bold red] Error interacting with RQ Worker objects: {e}", file=sys.stderr)
                    return False

        # --- Print Final Status Messages (after Live context closes) ---
        if final_status == "success":
            print("[green]All RQ workers are suspended.[/green]", file=sys.stderr)
            return True
        elif final_status == "no_workers":
            print("[green]No active RQ workers found registered in Redis.[/green]", file=sys.stderr)
            return True
        elif final_status == "timeout":
            # Fetch final non-suspended workers list for the message
            final_non_suspended_workers = []
            try:
                workers = Worker.all(connection=connection)
                final_non_suspended_workers = [w.name for w in workers if w.state != 'suspended']
            except Exception: pass  # Ignore errors during final check
            print(f"[yellow]Timeout reached.[/yellow] Not all workers reached 'suspended' state within {timeout}s.", file=sys.stderr)
            if final_non_suspended_workers:
                print(f"  Workers not suspended at timeout: {', '.join(final_non_suspended_workers)}", file=sys.stderr)
            return False
        else:  # Should not happen, but catch unexpected exit
            print("[red]Error:[/red] Worker wait loop exited unexpectedly.", file=sys.stderr)
            return False

    except Exception as e:
        print(f"\n[bold red]Error:[/bold red] Unexpected error during worker wait: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return False

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
