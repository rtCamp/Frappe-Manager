# Workers & Background Jobs

Frappe uses RQ (Redis Queue) to process background jobs in dedicated worker containers. Each worker type pulls from specific queues to handle different job workloads.

## Overview

Worker containers run independently from the web server and process jobs asynchronously:

- **Jobs** — Enqueued Python tasks (sending email, generating reports, processing imports)
- **Queues** — Named channels holding jobs (default, short, long, custom app queues)
- **Workers** — Container processes that pull jobs from queues and execute them

FM automatically creates worker containers based on:

1. **Built-in workers** — `short-worker`, `long-worker`, `schedule` (always present)
2. **Custom app queues** — Defined in app `hooks.py` (e.g., `background_jobs` dict)

!!! tip "Quick operations"
    **Restart workers:**
    ```bash
    fm restart mybench --workers
    ```
    
    **View worker logs:**
    ```bash
    fm logs mybench --service short-worker --follow
    ```
    
    **Check job status inside bench:**
    ```bash
    fm shell mybench -c "fmx rq status"
    ```

---

## Worker Types

FM creates these worker containers for every bench:

### `short-worker` {#short-worker}

**Queue:** `short`, `default`  
**Concurrency:** 4 processes  
**Purpose:** Quick background tasks (seconds to few minutes)

Handles jobs like:

- Sending individual emails
- Small data exports
- Cache invalidation
- Quick notifications

**Why separate from long-worker:** Prevents long-running jobs from blocking quick tasks.

---

### `long-worker` {#long-worker}

**Queue:** `long`  
**Concurrency:** 2 processes  
**Purpose:** Long-running background tasks (minutes to hours)

Handles jobs like:

- Bulk email sends
- Large report generation
- Data imports/exports (CSV, Excel)
- Backup operations

!!! warning "Interrupting long jobs"
    `fm restart` kills worker processes immediately. Any in-flight job is interrupted mid-execution. Use [safe restart workflow](#safe-worker-restarts) for production.

---

### `schedule` {#schedule}

**Queue:** N/A (cron-like scheduler)  
**Concurrency:** 1 process  
**Purpose:** Runs Frappe's scheduled tasks

Executes functions decorated with `@frappe.whitelist(allow_schedule=True)` based on their schedule (every hour, daily, weekly, cron expression).

**Not a queue worker:** Does not pull from RQ queues. Directly executes scheduled functions.

---

### Custom App Workers {#custom-app-workers}

Apps can define custom queues in `hooks.py`:

```python
# hooks.py
background_jobs = {
    "my_custom_queue": {
        "timeout": 600,
    }
}
```

FM automatically creates worker containers for custom queues. Container name format: `fm-<benchname>-my-custom-queue-1`

**See also:** [Frappe Framework — Background Jobs](https://frappeframework.com/docs/user/en/python-api/background-jobs)

`fm restart` kills worker processes immediately. Any in-flight job is interrupted.

For production — or any time you are running an email send, file import, or report that takes minutes — drain the queues first using [`fmx`](../guides/fmx.md):

```bash
# Wait for all in-flight jobs to finish, then restart
fm shell mybench -c "fmx restart --drain-workers"
```

To also run a database migration as part of the same step:

```bash
fm shell mybench -c "fmx restart --drain-workers --migrate"
```

See the [fmx guide](../guides/fmx.md) for the full list of restart options including maintenance mode and per-service restarts.

---

## Safe Worker Restarts

### The Problem

`fm restart mybench --workers` kills worker processes immediately, interrupting any in-flight job mid-execution.

**Consequences:**

- Half-sent email batches
- Incomplete data imports
- Corrupted report generation
- Transaction rollbacks

### Solution: Drain Workers First

Use `fmx restart --drain-workers` to wait for jobs to complete before restarting:

```bash
fm shell mybench -c "fmx restart --drain-workers"
```

**What happens:**

1. Workers finish current jobs
2. Workers refuse new jobs
3. All workers idle → restart triggered
4. Workers resume accepting jobs after restart

!!! tip "Combine with database migrations"
    ```bash
    fm shell mybench -c "fmx restart --drain-workers --migrate"
    ```
    
    Drains workers → migrates database → restarts all services.

**See also:** [fmx guide](../guides/fmx.md) for full restart options

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
workers = (CPU count × 2) + 1
```

**Examples:**

- 2-core machine → 5 workers
- 4-core machine → 9 workers
- 8-core machine → 17 workers

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

The `background_workers` setting (defaults to `1`) controls how many RQ worker processes handle each queue.

```bash
# Increase concurrency for all workers
fm shell mybench -c "bench set-config -g background_workers 2"
fm restart mybench --workers
```

**Effect:**

- `short-worker` runs 2 processes (4 with default `multi_queue_consumption`)
- `long-worker` runs 2 processes
- Custom app workers run 2 processes each

!!! tip "When to increase"
    Increase if you see job queues building up during peak hours. Monitor with:
    
    ```bash
    fm shell mybench -c "fmx rq status"
    ```
