# Background Jobs & Workers

Frappe uses RQ (Redis Queue) to process background jobs in dedicated worker containers. Each worker type pulls from specific queues to handle different job workloads.

## Overview

Worker containers run independently from the web server and process jobs asynchronously:

- **Jobs**: Enqueued Python tasks (sending email, generating reports, processing imports)
- **Queues**: Named channels holding jobs (default, short, long, custom app queues)
- **Workers**: Container processes that pull jobs from queues and execute them

FM runs each worker in its own container:

1. **Built-in workers**: `short-worker` and `long-worker` containers (always present)
2. **Custom queues**: one `<name>-worker` container per entry in the `workers` key of `common_site_config.json`

A separate `schedule` container runs [Frappe's scheduler](#the-scheduler); it enqueues jobs but is not a worker itself.

!!! tip "Quick operations"
    **Restart workers (leave web running):**
    ```bash
    fm restart mybench --workers --no-web
    ```
    
    **View worker logs** (all workers share `logs/worker.log` in the bench workspace):
    ```bash
    fm shell mybench -c "tail -f logs/worker.log"
    ```
    
    **Check job status inside bench:**
    ```bash
    fm shell mybench -c "fmx rq status"
    ```

---

## Worker Types

FM creates these worker containers for every bench:

### `short-worker` {#short-worker}

**Queues:** `short`, `default`  
**Concurrency:** `background_workers` processes (default 1)  
**Purpose:** Quick background tasks (seconds to few minutes)

Handles jobs like:

- Sending individual emails
- Small data exports
- Cache invalidation
- Quick notifications

**Why separate from long-worker:** Prevents long-running jobs from blocking quick tasks.

---

### `long-worker` {#long-worker}

**Queues:** `long`, `default`, `short`  
**Concurrency:** `background_workers` processes (default 1)  
**Purpose:** Long-running background tasks (minutes to hours); also picks up `default`/`short` jobs when `long` is empty

Handles jobs like:

- Bulk email sends
- Large report generation
- Data imports/exports (CSV, Excel)
- Backup operations

!!! warning "Interrupting long jobs"
    A plain `fm restart` drains workers first and aborts rather than kill a long job that exceeds the drain budget. If you must restart immediately, `--no-drain` interrupts the job (see [safe restart workflow](#safe-worker-restarts)).

---

### Custom App Workers {#custom-app-workers}

Define custom queues in `common_site_config.json` under the `workers` key:

```json
{
    "workers": {
        "myqueue": {
            "timeout": 5000,
            "background_workers": 1
        }
    }
}
```

FM generates a supervisor program (`bench worker --queue myqueue`) and a dedicated container for each entry when the bench's workers are (re)configured. Container name format: `fm__<benchname>__myqueue-worker` (dots in the bench name become underscores). `timeout` sets the worker's stop grace period; `background_workers` overrides the process count for that queue. `timeout` also serves as the supervisor stop grace for that worker's container-level restarts.

**See also:** [Frappe Framework: Background Jobs](https://frappeframework.com/docs/user/en/python-api/background-jobs)

---

## The Scheduler {#the-scheduler}

The `schedule` container runs `bench schedule`, Frappe's scheduler tick (a single process; it is not a queue worker).

At each tick it checks the `scheduler_events` declared in every installed app's `hooks.py` (hourly, daily, weekly, monthly, cron expressions) and **enqueues** the due jobs onto the RQ queues. The short/long workers pick up and run what it enqueues.

---

## Safe Worker Restarts

`fm restart` treats in-flight jobs explicitly. Workers drain by default:

| Mode | What happens to a running job | Speed |
|---|---|---|
| `fm restart mybench` (default) | never killed: workers are suspended via a Redis flag, fm waits for every in-flight job to finish (stale workers skipped, bounded at 300 seconds), then restarts and resumes; if jobs are still running when the budget expires, fm resumes the workers and aborts the restart (exit 1, nothing restarted) | normal |
| `fm restart mybench --no-drain` | interrupted, and fm says so: SIGUSR1 to each worker; a worker that has not exited after 15 seconds (tunable via `[workers].kill_timeout`) is escalated to a supervisor stop (SIGTERM, then SIGKILL when the stop grace expires); the job is marked failed or retried | fast, lossy |
| `fm restart mybench --force` | killed immediately along with everything else (supervisor stop + start) | fastest, lossiest |

If the drain wait exceeds its 300 second budget, fm does not kill anything: it resumes the workers and aborts the restart, so either raise `[workers].drain_timeout` in the [configuration reference](../reference/configuration.md#workers) or rerun with `--no-drain`. Only genuinely busy workers count against the gate: a worker holding no job that stops responding is declared stale after 15 seconds (`[workers].stale_timeout`) and skipped, so a dead worker cannot block restarts. The supervisor stop grace (stopwaitsecs: 360 seconds short queue, 1560 seconds long and default queues) is a separate safety net that only applies when a still-busy worker receives a stop signal (`--no-drain`, `--force`): SIGKILL ends the worker at that ceiling.

**Where docker fits.** These paths restart processes inside running containers. Only `fm restart mybench --container` recreates containers; there docker's stop timeout (then SIGKILL) applies to everything inside.

**Interrupted jobs are visible.** A job killed by `--no-drain`/`--force` lands in the failed-jobs registry; inspect with `fm shell mybench -c "fmx rq status"`.

!!! tip "Combine with database migrations"
    ```bash
    fm shell mybench -c "fmx restart --migrate"
    ```

    Drains workers (fmx drains by default, bounded at 300 seconds), migrates the database, then restarts all services.

**See also:** [fmx guide](../guides/fmx.md) for the in-container restart options (maintenance mode, per-service restarts, drain tuning)

---

## Pausing Workers

To temporarily stop workers from picking up new jobs without restarting (useful during manual database operations):

```bash
# Suspend all workers
fm shell mybench -c "fmx rq suspend"

# ... perform manual database work ...

# Resume workers
fm shell mybench -c "fmx rq resume"
```

!!! info "Existing jobs complete"
    Suspended workers finish their current job, then wait. They don't interrupt in-flight work.

## RQ Worker Concurrency

The `background_workers` setting in `common_site_config.json` (defaults to `1`) controls how many RQ worker processes run inside each worker container.

```bash
# Increase concurrency for all workers
fm shell mybench -c "bench set-config -g background_workers 2"
fm restart mybench --workers --no-web
```

**Effect:**

- `short-worker` runs 2 processes
- `long-worker` runs 2 processes
- Custom workers run 2 processes each, unless they set their own `background_workers` in the `workers` dict

!!! tip "When to increase"
    Increase if you see job queues building up during peak hours. Monitor with:
    
    ```bash
    fm shell mybench -c "fmx rq status"
    ```
