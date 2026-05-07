from enum import Enum
import sys
import time
import traceback
from typing import List, Optional, Tuple

import argparse
from rich import print
from rich.console import Console

import redis
from rq.exceptions import DeserializationError
from rq.suspension import is_suspended, resume, suspend
from rq.worker import Worker

from fmx.config import get_common_site_config_value

stderr_console = Console(stderr=True)


def _label(worker: Worker) -> str:
    """Get human-readable worker label. Tries: hostname:pid > queue name > UUID prefix."""
    try:
        if worker.hostname and worker.pid:
            return f"{worker.hostname}:{worker.pid}"
    except (AttributeError, TypeError):
        pass

    try:
        if hasattr(worker, 'queues') and worker.queues:
            queue_name = worker.queues[0].name
            if ':' in queue_name:
                return queue_name.split(':')[-1]
            return queue_name
    except (AttributeError, IndexError):
        pass

    return worker.name[:8]


def _get_redis_connection(redis_url=None):
    """Get Redis connection from argument or common_site_config.json."""
    if not redis_url:
        redis_url = get_common_site_config_value("redis_queue", verbose=False)
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


def _require_redis_connection(redis_url=None):
    """Get a Redis connection, raising RuntimeError if unavailable.

    Unlike ``_get_redis_connection``, this never returns ``None`` — it raises
    on failure so callers can rely on a single ``except Exception`` block instead
    of repeating a ``if not connection: return False`` guard.

    Args:
        redis_url: Optional Redis URL (falls back to common_site_config.json).

    Returns:
        Active Redis connection.

    Raises:
        RuntimeError: If the connection could not be established (error already printed).
    """
    connection = _get_redis_connection(redis_url=redis_url)
    if not connection:
        raise RuntimeError("Redis connection unavailable (see above for details)")
    return connection


def _is_worker_alive(worker: Worker, connection) -> bool:
    worker_hash = connection.hgetall(f'rq:worker:{worker.name}')
    if not worker_hash:
        return False

    state = worker.get_state()
    if state == '?':
        return False

    return True


_IDLE_STALE_SECONDS = 15


def _get_worker_states(connection) -> Tuple[List[Worker], List[Tuple[Worker, str]]]:
    all_workers = Worker.all(connection=connection)
    workers = [w for w in all_workers if _is_worker_alive(w, connection)]

    if not workers:
        return [], []

    non_suspended: List[Worker] = []
    states: List[Tuple[Worker, str]] = []

    for worker in workers:
        state = worker.get_state()
        states.append((worker, state))
        if state != 'suspended':
            non_suspended.append(worker)

    return non_suspended, states


class ActionEnum(str, Enum):
    suspend = "suspend"
    resume = "resume"


def is_rq_suspended(redis_url=None) -> bool:
    try:
        connection = _require_redis_connection(redis_url=redis_url)
        return bool(is_suspended(connection))
    except Exception:
        return False


def control_rq_workers(action: ActionEnum, redis_url=None) -> bool:
    """
    Connects to Redis using common_site_config.json and performs RQ suspension actions.

    Args:
        action: Action to perform (suspend/resume)
        redis_url: Optional Redis URL to use

    Returns:
        bool: True on success, False on failure
    """
    try:
        connection = _require_redis_connection(redis_url=redis_url)

        if action == ActionEnum.suspend:
            suspend(connection)
            if not is_suspended(connection):
                print(
                    f"\n[bold red]Error:[/bold red] Suspend command executed but Redis still shows not suspended",
                    file=sys.stderr,
                )
                return False
            return True
        elif action == ActionEnum.resume:
            resume(connection)
            if is_suspended(connection):
                print(
                    f"\n[bold red]Error:[/bold red] Resume command executed but Redis still shows suspended",
                    file=sys.stderr,
                )
                return False
            return True

        return False

    except Exception as e:
        print(f"\n[bold red]Error:[/bold red] Failed during RQ {action.value}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return False


def wait_for_rq_workers_suspended(
    timeout: int = 0,
    poll_interval: int = 5,
    redis_url=None,
    skip_stale: bool = True,
    stale_timeout: int = _IDLE_STALE_SECONDS,
) -> bool:
    try:
        connection = _require_redis_connection(redis_url=redis_url)

        start_time = time.time()
        final_status = "unknown"

        from rq import Queue

        while True:
            elapsed = time.time() - start_time

            if timeout > 0 and elapsed > timeout:
                final_status = "timeout"
                break

            try:
                non_suspended, worker_states = _get_worker_states(connection)

                if skip_stale:
                    filtered = []
                    for w, s in worker_states:
                        stale_idle = s == 'idle' and elapsed > stale_timeout
                        if stale_idle:
                            stderr_console.print(
                                f"[dim]  skip {_label(w)} state={s}  reason=idle too long after suspension[/dim]"
                            )
                        else:
                            filtered.append((w, s))
                    worker_states = filtered
                    non_suspended = [w for w, s in worker_states if s != 'suspended']

                if not worker_states:
                    final_status = "no_workers"
                    break

                workers_left = len(non_suspended)
                stderr_console.print(f"[dim]elapsed {elapsed:.0f}s  ·  {workers_left} worker(s) left[/dim]")
                for w, s in worker_states:
                    stderr_console.print(f"[dim]  worker {_label(w)}  state={s}[/dim]")

                for worker in non_suspended:
                    state = worker.get_state()
                    if state != 'busy':
                        try:
                            if hasattr(worker, 'queues') and worker.queues:
                                queue = worker.queues[0]
                                queue.enqueue(time.sleep, args=[0], at_front=True)
                                stderr_console.print(f"[dim]  → noop enqueued to {_label(worker)}[/dim]")
                        except Exception as enqueue_err:
                            stderr_console.print(
                                f"[yellow]warn:[/yellow] noop enqueue failed for {_label(worker)}: {enqueue_err}"
                            )

                if not non_suspended:
                    final_status = "success"
                    break

                time.sleep(poll_interval)

            except redis.RedisError as e:
                stderr_console.print(f"[bold red]Error:[/bold red] Redis operation failed: {e}")
                return False
            except Exception as e:
                stderr_console.print(f"[bold red]Error:[/bold red] RQ worker interaction failed: {e}")
                return False

        elapsed = time.time() - start_time

        if final_status == "success":
            stderr_console.print(f"[green]✔[/green] All workers suspended [dim]({elapsed:.1f}s)[/dim]")
            return True
        elif final_status == "no_workers":
            stderr_console.print("[green]✔[/green] No active RQ workers found in Redis.")
            return True
        elif final_status == "timeout":
            try:
                workers = Worker.all(connection=connection)
                laggards = [
                    _label(w)
                    for w in workers
                    if w.get_state() != 'suspended'
                    and (not skip_stale or not (w.get_state() == 'idle' and elapsed > stale_timeout))
                ]
            except Exception:
                laggards = []
            suffix = f" — {', '.join(laggards)}" if laggards else ""
            stderr_console.print(f"[red]✘[/red] Timeout after {timeout}s{suffix}")
            return False
        else:
            stderr_console.print("[red]Error:[/red] Worker wait loop exited unexpectedly.")
            return False

    except Exception as e:
        stderr_console.print(f"[bold red]Error:[/bold red] Unexpected error during worker wait: {e}")
        traceback.print_exc(file=sys.stderr)
        return False


def get_rq_worker_status(redis_url=None, include_dead: bool = False) -> Optional[dict]:
    try:
        connection = _require_redis_connection(redis_url=redis_url)

        from rq import Queue
        from rq.registry import FailedJobRegistry

        suspended = is_suspended(connection)
        all_workers = Worker.all(connection=connection)

        all_queues = Queue.all(connection=connection)
        active_queue_names = {q for w in all_workers for q in w.queue_names()}
        queue_list = []
        for q in sorted(all_queues, key=lambda q: q.name):
            if q.name not in active_queue_names:
                continue
            pending = len(q)
            failed = 0
            try:
                failed = FailedJobRegistry(q.name, connection=connection).count
            except Exception:
                pass
            queue_list.append((q.name, pending, failed))

        if include_dead:
            workers = all_workers
        else:
            workers = [w for w in all_workers if _is_worker_alive(w, connection)]

        worker_list = []
        dead_worker_list = []

        for worker in workers:
            is_alive = _is_worker_alive(worker, connection)
            state = worker.get_state()
            current_job = None
            if state == 'busy':
                job = worker.get_current_job()
                if job:
                    try:
                        current_job = job.func_name
                    except DeserializationError:
                        # Module no longer exists; use safe job identifier instead
                        current_job = job.description or job.id or "<unresolvable job>"

            queue_names = worker.queue_names()
            queue_count = 0
            if queue_names:
                for queue_name in queue_names:
                    try:
                        queue = Queue(queue_name, connection=connection)
                        queue_count += len(queue)
                    except Exception:
                        pass

            job_stats = {
                'successful': getattr(worker, 'successful_job_count', 0),
                'failed': getattr(worker, 'failed_job_count', 0),
                'working_time': getattr(worker, 'total_working_time', 0.0),
                'queue_count': queue_count,
            }
            job_stats['total'] = job_stats['successful'] + job_stats['failed']

            last_heartbeat = getattr(worker, 'last_heartbeat', None)

            worker_entry = (_label(worker), state, current_job, job_stats, last_heartbeat)

            if is_alive:
                worker_list.append(worker_entry)
            else:
                dead_worker_list.append(worker_entry)

        result = {'suspended': bool(suspended), 'workers': worker_list, 'queues': queue_list}
        if include_dead and dead_worker_list:
            result['dead_workers'] = dead_worker_list

        return result

    except Exception as e:
        print(
            f"\n[bold red]Error (get_rq_worker_status):[/bold red] Failed to get RQ worker status: {e}",
            file=sys.stderr,
        )
        if __debug__:
            traceback.print_exc(file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="RQ Worker Controller CLI")
    parser.add_argument("--redis-url", type=str, default=None, help="Redis URL to use (overrides config file)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # suspend
    subparsers.add_parser("suspend", help="Suspend all RQ workers")

    # resume
    subparsers.add_parser("resume", help="Resume all RQ workers")

    # wait
    wait_parser = subparsers.add_parser("wait", help="Wait for all RQ workers to be suspended")
    wait_parser.add_argument("--timeout", type=int, default=0, help="Timeout in seconds. 0 (default) = wait indefinitely.")
    wait_parser.add_argument("--poll-interval", type=int, default=5, help="Polling interval in seconds (default: 5)")

    # check
    subparsers.add_parser("check", help="Check if RQ workers are suspended")

    args = parser.parse_args()

    if args.command == "suspend":
        sys.exit(0 if control_rq_workers(ActionEnum.suspend, redis_url=args.redis_url) else 1)
    elif args.command == "resume":
        sys.exit(0 if control_rq_workers(ActionEnum.resume, redis_url=args.redis_url) else 1)
    elif args.command == "wait":
        ok = wait_for_rq_workers_suspended(
            timeout=args.timeout, poll_interval=args.poll_interval, redis_url=args.redis_url
        )
        sys.exit(0 if ok else 1)
    elif args.command == "check":
        result = is_rq_suspended(redis_url=args.redis_url)
        print("suspended" if result else "not suspended")
        sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
