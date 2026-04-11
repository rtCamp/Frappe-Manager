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


**Examples**:

_Restart web and workers (default)_
Restarts both web and worker services for the bench. Safe for applying configuration changes.
```bash
fm restart mybench
```

_Restart via container restart_
Restarts by restarting the entire Docker containers (slower but thorough).
```bash
fm restart mybench --container
```

_Restart via supervisor (faster)_
Uses supervisor to restart processes inside containers for a faster restart without recreating containers.
```bash
fm restart mybench --supervisor
```

_Restart web services only_
Restarts only web-related services (frappe, socketio) without touching workers.
```bash
fm restart mybench --web --no-workers
```

_Restart workers only_
Restarts worker processes (schedule, long/short workers) while leaving web services running.
```bash
fm restart mybench --workers --no-web
```

_Force restart (immediate kill)_
Performs an immediate kill and restart; use when processes are unresponsive.
```bash
fm restart mybench --force
```

_Restart redis services_
Restarts Redis instances used by the bench (cache and queue backends).
```bash
fm restart mybench --redis
```

_Restart nginx service_
Restarts the nginx service for the bench, useful after configuration changes to proxy or TLS.
```bash
fm restart mybench --nginx
```

