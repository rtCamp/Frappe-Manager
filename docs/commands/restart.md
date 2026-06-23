## `fm restart`

Restart bench services (web, workers, redis, nginx).

Choose between container-level restarts or supervisor-level restarts for faster in-container restarts.

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
* `--supervisor`: Restart supervisor processes inside container. Faster than container restart.
* `--force`: Force restart: --supervisor uses stop+start (kills processes), --container uses timeout=0 (immediate kill).
* `--graceful`: Reload the frappe (gunicorn) web container in place by sending SIGHUP via supervisorctl instead of stop/start. The gunicorn master keeps its listening socket open across the reload, so upstream proxies see no connection-refused window. Applies only to supervisor mode; ignored with --container. The socketio container is restarted normally (node has no graceful-reload signal). Mutually exclusive with --force.


## Examples

### Restart web and workers (default)

Restarts both web and worker services for the bench. Safe for applying configuration changes.

```bash
fm restart mybench
```

### Restart via container restart

Restarts by restarting the entire Docker containers (slower but thorough).

```bash
fm restart mybench --container
```

### Restart via supervisor (faster)

Uses supervisor to restart processes inside containers for a faster restart without recreating containers.

```bash
fm restart mybench --supervisor
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

### Graceful web reload (no upstream connection-refused window)

Delivers SIGHUP to the frappe (gunicorn) web container via supervisorctl instead of stop/start. The gunicorn master keeps its listening socket open while it spawns fresh workers and then retires the old ones, so an upstream proxy never observes a closed listener. The socketio container is restarted normally because node has no graceful-reload signal. Only affects supervisor mode; ignored when combined with --container.

Caveat: the web container runs gunicorn with `--preload`, so SIGHUP recycles workers but does not re-import application code in the master. Use this flag when you want to restart workers without dropping the upstream listener (for example to release leaked memory or apply runtime config); use a full restart when you need new application code to take effect.

```bash
fm restart mybench --graceful
```

