## `fm restart`

Restart bench services. Web and workers by default; redis/nginx are opt-in: rarely needed, and a redis restart briefly disconnects every consumer (in-flight jobs can fail; data itself persists via volumes + RDB).

Workers drain by default: fm suspends them, waits for in-flight jobs (bounded by \[workers].drain_timeout), restarts, and resumes. If jobs do not finish in time the restart is ABORTED with workers resumed; nothing is killed implicitly. --no-drain interrupts running jobs explicitly; --force kills everything fast.

Mechanisms: in-container process restart via supervisor (default, fastest), --container (full container stop/start, thorough; also starts a stopped bench), --rolling (zero-downtime web recreate; image benches).

**Usage**:

```console
$ fm restart BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--web`: Restart the web tier (frappe server and socketio).
* `--workers`: Restart the worker tier (schedule and all RQ workers).
* `--redis`: Restart redis services (opt-in: briefly disconnects every consumer).
* `--nginx`: Restart the bench nginx service (opt-in: useful after proxy or TLS config changes).
* `--container`: Restart whole Docker containers instead of supervisor processes (slower, thorough; also starts a stopped bench).
* `--force`: Kill everything fast: supervisor stop+start (default mode) or container stop with timeout=0 (--container). Implies --no-drain; conflicts with explicit --drain and --rolling.
* `--rolling`: Zero-downtime web-tier recreate on the current image tag (image benches only). Workers still drain and cycle normally.
* `--drain/--no-drain`: Wait for in-flight RQ jobs to finish before restarting workers; abort the restart if they do not finish within \[workers].drain_timeout. --no-drain skips the wait and interrupts running jobs.
* `--service`: Restart only the named service(s) (repeatable); overrides the group flags and skips draining. Any service from the bench or workers compose.


## Examples

### Restart web and workers (default)

Drains workers first (waits for in-flight jobs, bounded by [workers].drain_timeout), then restarts web and workers. Aborts rather than kill a job that does not finish in time.

```bash
fm restart mybench
```

### Restart without waiting for jobs

Skips the drain wait and interrupts in-flight jobs explicitly (SIGUSR1, then force-stop after [workers].kill_timeout seconds); interrupted jobs land in the failed-jobs registry.

```bash
fm restart mybench --no-drain
```

### Restart one service only

Targets a single service instead of a group; repeat --service for several. Skips draining. Code services restart via supervisor, infra services (nginx, redis) via container.

```bash
fm restart mybench --service socketio
```

### Restart via container restart

Restarts by restarting the entire Docker containers (slower but thorough).

```bash
fm restart mybench --container
```

### Zero-downtime web restart (image bench)

Recreates web containers on the current tag via the deploy engine's rolling swap: new replicas serve before old ones drain.

```bash
fm restart mybench --rolling
```

### Restart web services only

Restarts only web-related services (frappe, socketio) without touching workers.

```bash
fm restart mybench --web --no-workers
```

### Restart workers only

Restarts worker processes (schedule, long/short workers) while leaving web services running.

```bash
fm restart mybench --workers --no-web
```

### Force restart (immediate kill)

Immediate kill and restart for unresponsive processes. Skips draining; in-flight jobs are interrupted and marked failed or retried.

```bash
fm restart mybench --force
```

### Restart redis services

Restarts Redis instances used by the bench (cache and queue backends).

```bash
fm restart mybench --redis
```

### Restart nginx service

Restarts the nginx service for the bench, useful after configuration changes to proxy or TLS.

```bash
fm restart mybench --nginx
```

## Related

- [fmx: In-Container Service Manager](../guides/fmx.md)
