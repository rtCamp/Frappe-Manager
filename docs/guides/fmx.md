# fmx: In-Container Service Manager

`fmx` is a small CLI tool that lives **inside** every Frappe Manager container. While `fm` is the host-side tool you use to create, start, and manage benches from your terminal, `fmx` is what controls the individual processes *running inside* a bench: the web server, workers, scheduler, and socket server.

You will not normally need `fmx` for day-to-day development. It becomes useful when you want to restart a single service without touching the whole bench, drain workers safely before a database migration, or debug a process that is stuck.

## How it fits in

```
Your terminal
    │
    ▼
fm (host CLI)
    │  docker exec / compose exec
    ▼
frappe container
    │
    ▼
fmx (in-container CLI)
    │  Unix socket (supervisord)
    ▼
supervisord → frappe · short-worker · long-worker · schedule · socketio
```

`fm` manages Docker containers. `fmx` manages the supervisor-controlled processes **inside** one of those containers. Every bench shares the same container image, so `fmx` is always available.

## Running fmx

From the host, pass a command through `fm shell`:

```bash
fm shell mybench -c "fmx status"
```

Or open an interactive shell first, then run fmx directly:

```bash
fm shell mybench
fmx status
```

!!! tip
    The `fmx` binary lives in the container's `PATH`, so no special path is needed once you are inside the shell.

## Services fmx controls

| Name | What it is |
|---|---|
| `frappe` | Gunicorn web server (serves HTTP requests) |
| `short-worker` | RQ worker for short background jobs |
| `long-worker` | RQ worker for long-running jobs |
| `schedule` | Frappe scheduler (triggers recurring tasks) |
| `socketio` | Socket.IO server (realtime / desk notifications) |

## Commands

### `fmx status`: see what is running

```bash
fmx status
```

Shows the supervisor state of every service and the live RQ worker status: suspend flag, queue depths, and registered workers.

```bash
fmx status --verbose
```

Adds per-process detail: PID, uptime, current job per worker, and queue assignments. Useful when a queue has an unexpected backlog or a process looks stuck.

---

### `fmx start`: start services

```bash
# Start everything
fmx start

# Start only the scheduler
fmx start schedule

# Start a specific process instance within a service
fmx start short-worker -p short-worker_1
```

---

### `fmx stop`: stop services

```bash
# Stop everything immediately (running jobs are interrupted)
fmx stop

# Stop only the workers, leave web running
fmx stop short-worker long-worker

# Stop a single process instance
fmx stop long-worker -p long-worker_1
```

#### Draining is opt-in for `stop`

`fmx stop` halts services immediately; running jobs are interrupted. If workers may be processing jobs you cannot afford to lose (email sends, report generation, file imports), drain them first:

```bash
fmx stop --drain-workers
```

This sets a Redis suspend flag so workers stop picking up new jobs, then waits for every worker to finish its current job before stopping them. For `stop` the wait is unbounded by default; a worker idle for longer than `--skip-stale-timeout` (15 seconds) is treated as stale and skipped, so one hung worker cannot hold the stop open forever. If the wait does run out, fmx resumes the workers and aborts instead of stopping them mid-job.

```bash
# Bound the wait window instead of waiting indefinitely
fmx stop --drain-workers --drain-workers-timeout 600

# Poll less often on a busy system (default is every 5 seconds)
fmx stop --drain-workers --drain-workers-poll 10
```

---

### `fmx restart`: restart services

This is the most-used `fmx` command. It stops and then starts the targeted services in parallel. **Draining is the default**: workers stop picking up new jobs, fmx waits for in-flight jobs to finish (bounded at 300 seconds; `--drain-workers-timeout` tunes it, `0` waits indefinitely), then restarts. Workers idle for longer than `--skip-stale-timeout` (15 seconds) are skipped so a hung worker cannot block the wait, and if the wait runs out fmx resumes the workers and aborts the restart rather than interrupt a job.

!!! note "`fm restart` and `fmx restart` are the same guarantee from two sides"
    `fm restart BENCHNAME` runs on the host and owns the drain: it suspends workers through fmx's RQ controller, waits up to [`[workers] drain_timeout`](../reference/configuration.md#workers) (300 seconds by default), and aborts the restart rather than kill a job that outlasts it. The fmx call it then makes inside each container passes `--no-drain-workers`, so nothing waits twice. Running `fmx restart` yourself gives you the same drain with the same 300 second default, scoped to the container you are in. The suspend flag lives in Redis, so workers restarted mid-drain come back suspended until the resume; ordering holds across the restart. The same gate guards `fm switch`.

=== "Safe (default)"

    ```bash
    fmx restart
    ```

    Workers stop picking up new jobs, fmx waits for the jobs in flight to finish, then restarts. A job that outlasts the deadline aborts the restart instead of being interrupted.

=== "Fast (dev)"

    ```bash
    fmx restart --no-drain-workers
    ```

    Skips the drain: each worker gets SIGUSR1 (RQ's warm shutdown) and is stopped through supervisor if it is still running afterwards. Any in-flight job may be interrupted. Fine for development where speed matters.

=== "With DB migration"

    ```bash
    fmx restart --migrate
    ```

    The safest production deploy sequence:

    1. Suspend workers: they stop accepting new jobs
    2. Wait for in-flight jobs to finish
    3. Run `bench migrate` (override with `--migrate-command`)
    4. Restart all services

    If the migration fails, the flow aborts and starts back whatever it had stopped.

=== "With maintenance page"

    ```bash
    fmx restart --migrate \
        --maintenance-mode drain \
        --maintenance-mode migrate
    ```

    Same as above but sets `maintenance_mode=1` in `common_site_config.json` for each phase, so Frappe serves its built-in maintenance page to users. Always cleared on completion, even if something crashes mid-way.

#### Restart only specific services

```bash
# Restart workers only: leaves web and socketio running
fmx restart short-worker long-worker
```

#### Tuning the kill window

With a drain, workers are stopped through supervisor and these two flags do nothing. They apply to the `--no-drain-workers` path, where each worker gets SIGUSR1 and then a supervisor stop if it is still alive:

```bash
# Allow 30 seconds for the warm shutdown before escalating to a supervisor stop
fmx restart --no-drain-workers --worker-kill-timeout 30 --worker-kill-poll 2
```

---

### `fmx rq`: manage RQ workers directly

For manual maintenance windows where you want to pause workers without restarting anything:

```bash
# Stop workers from picking up new jobs
fmx rq suspend

# Check suspend state and queue depths
fmx rq status

# Verbose: show active job per worker
fmx rq status --verbose

# Resume: workers start processing again immediately
fmx rq resume
```

!!! note "When `fmx rq suspend` is useful"
    Before running a manual SQL migration or patching a custom app, suspend workers first so no background job touches the database while you are making changes.

## Global options

| Flag | Description |
|---|---|
| `--verbose` / `-v` | More detail in status output |
| `--debug` | Show full stack traces on errors |
| `--version` | Print fmx version |

## Where fmx comes from

`fmx` is built into the `ghcr.io/rtcamp/frappe-manager-frappe` Docker image; you do not install it separately. Its source code lives at `Docker/frappe/fmx/` inside the Frappe Manager repository.

It communicates with each supervisor instance through a Unix socket at `/fm-sockets/{service}.sock` inside the container. The `fm-sockets` Docker volume is shared between the frappe container and other service containers so they can all be managed through the same interface.

## VSCode integration

When you use `fm code mybench` to open a bench in VS Code, the generated `.vscode/tasks.json` includes an `fm-kill-port` task that runs:

```bash
fmx stop frappe && sleep 2
```

This stops the Gunicorn web server before the VS Code debugger attaches, necessary because the debugger needs to bind to port 80 itself.
