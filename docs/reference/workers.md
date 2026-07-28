# Workers & Background Jobs

Frappe uses RQ (Redis Queue) to process background jobs in dedicated worker containers. Each worker type pulls from specific queues to handle different job workloads.

## Overview

Worker containers run independently from the web server and process jobs asynchronously:

- **Jobs** — Enqueued Python tasks (sending email, generating reports, processing imports)
- **Queues** — Named channels holding jobs (default, short, long, custom app queues)
- **Workers** — Container processes that pull jobs from queues and execute them

FM runs each worker in its own container:

1. **Built-in workers** — `short-worker` and `long-worker` containers (always present), plus the `schedule` container that runs the scheduler
2. **Custom queues** — one `<name>-worker` container per entry in the `workers` key of `common_site_config.json`

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
    A plain `fm restart` does not wait for in-flight jobs. Use `fm restart mybench --drain` (see [safe restart workflow](#safe-worker-restarts)) for production.

---

### `schedule` {#schedule}

**Queue:** N/A (cron-like scheduler)  
**Concurrency:** 1 process  
**Purpose:** Runs `bench schedule`, Frappe's scheduler tick

At each tick it checks the `scheduler_events` declared in every installed app's `hooks.py` (hourly, daily, weekly, monthly, cron expressions) and **enqueues** the due jobs onto the RQ queues.

**Not a queue worker:** It does not execute jobs itself — the short/long workers pick up and run what it enqueues.

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

FM generates a supervisor program (`bench worker --queue myqueue`) and a dedicated container for each entry when the bench's workers are (re)configured. Container name format: `fm__<benchname>__myqueue-worker` (dots in the bench name become underscores). `timeout` sets the worker's stop grace period; `background_workers` overrides the process count for that queue.

**See also:** [Frappe Framework — Background Jobs](https://frappeframework.com/docs/user/en/python-api/background-jobs)


---

## Safe Worker Restarts

### The Problem

`fm restart` (with or without `--workers`) restarts worker processes without waiting for in-flight jobs, interrupting any running job mid-execution.

**Consequences:**

- Half-sent email batches
- Incomplete data imports
- Corrupted report generation
- Transaction rollbacks

### Solution: Drain Workers First

Use `fm restart mybench --drain` to wait for in-flight jobs to complete before restarting the workers:

```bash
fm restart mybench --drain
```

**What happens:**

1. Workers are suspended via a Redis flag — they refuse new jobs
2. FM waits for every in-flight job to finish (stale idle workers are skipped so a hung worker cannot block forever)
3. Workers restart
4. Workers resume accepting jobs

!!! tip "Combine with database migrations"
    ```bash
    fm shell mybench -c "fmx restart --migrate"
    ```

    Drains workers (fmx drains by default) → migrates database → restarts all services.

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

---

## Gunicorn Web Workers

The production web server (Gunicorn) runs multiple worker processes to handle concurrent HTTP requests. **This is different from RQ background workers.**

### Worker Count Formula

FM sizes Gunicorn workers automatically:

```
workers = min(CPU count, RAM in MB / 256)
```

i.e. one worker per CPU core, capped so each worker has roughly 256 MB of RAM available. Workers use the `gthread` class, so each worker additionally serves multiple concurrent requests via threads (default threads: `max(2, min(CPU count, 4))`, overridable with `gunicorn_threads` in `common_site_config.json`).

**Examples:**

- 4-core machine, 8 GB RAM → 4 workers (CPU-bound)
- 8-core machine, 1 GB RAM → 4 workers (RAM-bound: 1024 MB / 256)
- 2-core machine, 512 MB RAM → 2 workers

### Overriding Worker Count

Set `gunicorn_workers` in `common_site_config.json`:

```bash
fm shell mybench -c "bench set-config -g gunicorn_workers 4"
fm restart mybench
```

!!! warning "Too few = slow, too many = OOM"
    - **Too few workers:** Requests queue up, slow response times
    - **Too many workers:** Excessive RAM usage, potential OOM kills
    
    The default formula balances CPU utilization and memory.

---

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
