## `fm restart`

Restart bench services. Web and workers by default; redis/nginx are opt-in: rarely needed, and a redis restart briefly disconnects every consumer (in-flight jobs can fail; data itself persists via volumes + RDB).

Three modes: in-container process restart via supervisor (default, fastest), --container (full container stop/start, thorough), --rolling (zero-downtime web recreate; image benches).

**Usage**:

```console
$ fm restart BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--web`: Restart web service i.e socketio and frappe server.
* `--workers`: Restart worker services i.e schedule and all workers.
* `--redis`: Restart redis services.
* `--nginx`: Restart nginx service.
* `--container`: Restart entire Docker container(s). Stops and starts the container.
* `--force`: Force restart: kills processes (default mode) / stops containers with timeout=0 (--container).
* `--rolling`: Zero-downtime web-tier recreate on the current image tag (image benches only).
* `--drain`: Wait for in-flight RQ jobs to finish before restarting workers (graceful).
* `--service`: Restart only the named service(s) (repeatable); overrides the group flags. Any service from the bench or workers compose.


## Examples

### Restart web and workers (default)

Restarts both web and worker services for the bench. Safe for applying configuration changes.

```bash
fm restart mybench
```

### Restart one service only

Targets a single service instead of a group; repeat --service for several. Code services restart via supervisor, infra services (nginx, redis) via container.

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

Performs an immediate kill and restart; use when processes are unresponsive.

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

