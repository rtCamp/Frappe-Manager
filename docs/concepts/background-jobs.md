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
    
    **View worker logs** (every worker program writes to `logs/worker.log`, with tracebacks in `logs/worker.error.log`, both in the bench workspace):
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

**Queues:** `short`, then `default` (the order is the priority order RQ pulls in)  
**Concurrency:** `background_workers` processes (default 1)  
**Job timeout:** 300 seconds for both queues (Frappe's default)  
**Purpose:** Quick background tasks (seconds to few minutes)

Handles jobs like:

- Sending individual emails
- Small data exports
- Cache invalidation
- Quick notifications

**Why separate from long-worker:** Prevents long-running jobs from blocking quick tasks.

---

### `long-worker` {#long-worker}

**Queues:** `long`, then `default`, then `short`  
**Concurrency:** `background_workers` processes (default 1)  
**Job timeout:** 1500 seconds on `long`, 300 on `default`/`short` (Frappe's defaults)  
**Purpose:** Long-running background tasks (minutes to hours); it also picks up `default`/`short` jobs, but only when `long` has nothing waiting

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

FM turns each entry into a supervisor program (`bench worker --queue myqueue`, where `--queue` is bench's own flag) plus a dedicated container, generated when the bench's workers are (re)configured. Container name format: `fm__<benchname>__myqueue-worker` (dots in the bench name become underscores).

- `timeout` (default 300) does double duty: Frappe reads it as the job timeout for that queue, and fm renders it as the worker's supervisor stop grace (`stopwaitsecs`).
- `background_workers` overrides the process count for this queue only. Omit it and the global `background_workers` applies.

fm validates the `workers` key before it reaches the supervisor template, so a mistake fails the regeneration loudly instead of producing a container that only breaks once it is running:

- queue names take letters, digits, `-` and `_`, and must start with a letter or a digit;
- `default`, `short`, `long` and `schedule` are reserved: they would collide with the built-in programs;
- `timeout` and `background_workers` must be at least 1, and no other key is accepted inside an entry.

Adding or removing a queue changes the set of containers, so it needs a regeneration pass: `fm start mybench --reconfigure-workers`. Removed queues have their container stopped and their supervisor conf deleted.

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

Raising the budget is `[workers].drain_timeout` in the [configuration reference](../reference/configuration.md#workers); the alternative is `--no-drain`. Only genuinely busy workers count against the gate: a worker holding no job that stops responding is declared stale after 15 seconds (`[workers].stale_timeout`) and skipped, so a dead worker cannot block restarts.

The supervisor stop grace (`stopwaitsecs`) is a separate safety net, and it only applies when a still-busy worker is sent a stop signal (`--no-drain`, `--force`, or the escalation above): supervisor SIGKILLs the worker at that ceiling. For the built-in workers fm sets it a minute past the queue's own job timeout, so an overrunning job hits RQ's timeout first: 360 seconds on `short-worker`, 1560 on `long-worker`. A custom worker gets its `timeout` verbatim, so set that above the longest job you enqueue on its queue.

**The suspend flag outlives the restart.** Draining suspends RQ through a persistent Redis key, so workers that come back mid-drain come back suspended, and fm resumes them at the end. That is also why an aborted drain resumes explicitly rather than relying on the restart.

**An image without fmx cannot be drained.** The drain runs inside the frappe container, so an image predating fmx has nothing to run: fm warns and restarts undrained instead of reporting a phantom timeout. `fm self update-images` installs an image that can drain.

**Where docker fits.** These paths restart processes inside running containers. Only `fm restart mybench --container` restarts the containers themselves; there docker's stop timeout applies to everything inside (100 seconds, or 0 with `--force`) before SIGKILL.

**Interrupted jobs are visible.** A job killed by `--no-drain`/`--force` lands in RQ's failed-job registry; `fm shell mybench -c "fmx rq status"` reports the failed count per queue.

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

The `background_workers` setting in `common_site_config.json` (defaults to `1`) controls how many RQ worker processes run inside each worker container. It reaches the container as `numprocs` in the generated supervisor conf, which supervisord reads once, at startup, so changing it takes three steps:

```bash
fm shell mybench -c "bench set-config -g background_workers 2"
fm start mybench --reconfigure-workers               # regenerate the supervisor confs from the new value
fm restart mybench --workers --no-web --container    # supervisord re-reads them on container start
```

A plain `fm restart` is not enough on its own: it restarts the supervisor programs inside the running containers, and supervisord keeps the process count it booted with.

**Effect:**

- `short-worker` runs 2 processes
- `long-worker` runs 2 processes
- Custom workers run 2 processes each, unless they set their own `background_workers` in the `workers` dict

!!! tip "When to increase"
    Increase if you see job queues building up during peak hours. Monitor with:
    
    ```bash
    fm shell mybench -c "fmx rq status"
    ```
