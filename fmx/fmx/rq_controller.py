import contextlib
from enum import Enum
import json
from pathlib import Path
import sys
import time
import traceback
from typing import Optional
from typing import Any, Optional

from rich import print
from rich import print
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

import redis
from rq import Queue
from rq.suspension import is_suspended, resume, suspend
from rq.worker import Worker

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


def _get_redis_connection():
    """Get Redis connection from common_site_config.json"""
    redis_url = _get_site_config_key_value("redis_queue", verbose=False)
    if not redis_url:
        print("[bold red]Error:[/bold red] 'redis_queue' URL not found in common_site_config.json.", file=sys.stderr)
        return None

    try:
        connection = redis.from_url(redis_url)
        connection.ping()
        return connection
    except (redis.exceptions.ConnectionError, ValueError) as e:
        print(f"[bold red]Error:[/bold red] Failed to connect to Redis: {e}", file=sys.stderr)
        return None

def _get_worker_states(connection):
    """Get all workers and their states from Redis"""
    workers = Worker.all(connection=connection)
    if not workers:
        return [], []
    
    non_suspended_workers = []
    worker_states = []
    
    for worker in workers:
        state = worker.state
        worker_states.append((worker.name, state))
        if state != 'suspended':
            non_suspended_workers.append(worker)
    
    return non_suspended_workers, worker_states


def noop():
    """Does absolutely nothing. Used to wake up idle RQ workers."""
    pass

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
    try:
        connection = _get_redis_connection()
        if not connection:
            return False

        if action == ActionEnum.suspend:
            print(f"[cyan]Suspending RQ workers via Redis flag...[/cyan]", file=sys.stderr)
            suspend(connection)
            print(f"[green]Successfully set suspension flag in Redis.[/green]", file=sys.stderr)
            return True
        elif action == ActionEnum.resume:
            print(f"[cyan]Resuming RQ workers via Redis flag...[/cyan]", file=sys.stderr)
            result = resume(connection)
            if result == 1:
                print(f"[green]Successfully removed suspension flag in Redis.[/green]", file=sys.stderr)
            else:
                print(f"[dim]Suspension flag was not present in Redis.[/dim]", file=sys.stderr)
            return True

    except Exception as e:
        print(f"\n[bold red]Error:[/bold red] Failed during RQ {action.value}: {e}", file=sys.stderr)
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
    try:
        connection = _get_redis_connection()
        if not connection:
            return False

        print(f"Waiting up to {timeout}s for RQ workers to reach 'suspended' state...")
        start_time = time.time()
        final_status = "unknown"

        with Live(vertical_overflow="visible") as live:
            while True:
                verbose_messages = []

                if (time.time() - start_time) > timeout:
                    final_status = "timeout"
                    break

                try:
                    non_suspended_workers_list, worker_states = _get_worker_states(connection)
                    if not worker_states:  # No workers at all
                        final_status = "no_workers"
                        break

                    if non_suspended_workers_list:
                        if verbose:
                            verbose_messages.append(f"[dim]Found {len(non_suspended_workers_list)} non-suspended worker(s). Attempting to enqueue noop jobs...[/dim]")
                        for worker in non_suspended_workers_list:
                            if worker.state != 'busy':
                                try:
                                    listened_queue_names = worker.queue_names()
                                    if listened_queue_names:
                                        target_queue_name = listened_queue_names[0]
                                        queue = Queue(target_queue_name, connection=connection)
                                        queue.enqueue('fm_helper.rq_controller.noop', at_front=True)
                                        if verbose:
                                            verbose_messages.append(f"[dim]  - Enqueued noop to '{target_queue_name}' for worker '{worker.name}' (state: {worker.state}).[/dim]")
                                except Exception as enqueue_err:
                                    if verbose:
                                        verbose_messages.append(f"[yellow]Warning:[/yellow] Failed to enqueue noop job for worker '{worker.name}': {enqueue_err}")

                    non_suspended_worker_names = [w.name for w in non_suspended_workers_list]
                    if not non_suspended_worker_names:
                        final_status = "success"
                        break

                    current_time = time.time()
                    elapsed_time = current_time - start_time
                    remaining_time = max(0, timeout - elapsed_time)
                    summary_text = (
                        f"Elapsed: [cyan]{elapsed_time:.1f}s[/] | Remaining: [cyan]{remaining_time:.1f}s[/]\n"
                        f"Waiting for [yellow]{len(non_suspended_worker_names)}[/] worker(s) to suspend: {', '.join(non_suspended_worker_names)}"
                    )
                    summary_panel = Panel(summary_text, title="RQ Worker Wait Status", border_style="blue", expand=False)

                    renderables = [summary_panel]
                    if verbose:
                        if verbose_messages:
                            verbose_output = "\n".join(verbose_messages)
                            renderables.append(Panel(verbose_output, title="Verbose Log", border_style="dim", expand=False))

                        worker_table = Table(title="Worker States", expand=False)
                        worker_table.add_column("Worker Name", style="dim")
                        worker_table.add_column("State")

                        for name, state in worker_states:
                            state_color = "green" if state == 'suspended' else "yellow" if state in ('idle', 'busy') else "red"
                            worker_table.add_row(name, f"[{state_color}]{state}[/{state_color}]")

                        renderables.append(worker_table)

                    render_group = Group(*renderables)
                    live.update(render_group)

                    time.sleep(poll_interval)

                except redis.RedisError as e:
                    print(f"\n[bold red]Error:[/bold red] Redis operation failed while checking workers: {e}", file=sys.stderr)
                    return False
                except Exception as e:
                    print(f"\n[bold red]Error:[/bold red] Error interacting with RQ Worker objects: {e}", file=sys.stderr)
                    return False

        if final_status == "success":
            print("[green]All RQ workers are suspended.[/green]", file=sys.stderr)
            return True
        elif final_status == "no_workers":
            print("[green]No active RQ workers found registered in Redis.[/green]", file=sys.stderr)
            return True
        elif final_status == "timeout":
            final_non_suspended_workers = []
            try:
                workers = Worker.all(connection=connection)
                final_non_suspended_workers = [w.name for w in workers if w.state != 'suspended']
            except Exception: pass
            print(f"[yellow]Timeout reached.[/yellow] Not all workers reached 'suspended' state within {timeout}s.", file=sys.stderr)
            if final_non_suspended_workers:
                print(f"  Workers not suspended at timeout: {', '.join(final_non_suspended_workers)}", file=sys.stderr)
            return False
        else:
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
    try:
        connection = _get_redis_connection()
        if not connection:
            return None

        suspended = is_suspended(connection)
        return bool(suspended)

    except Exception as e:
        print(f"\n[bold red]Error (check_rq_suspension):[/bold red] Failed during RQ suspension check: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None
