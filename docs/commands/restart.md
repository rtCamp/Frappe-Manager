## `fm restart`

Restart bench services (web, workers, redis, nginx)

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
```bash
fm restart mybench
```

_Restart via container restart_
```bash
fm restart mybench --container
```

_Restart via supervisor (faster)_
```bash
fm restart mybench --supervisor
```

_Restart web services only_
```bash
fm restart mybench --web --no-workers
```

_Restart workers only_
```bash
fm restart mybench --workers --no-web
```

_Force restart (immediate kill)_
```bash
fm restart mybench --force
```

_Restart redis services_
```bash
fm restart mybench --redis
```

_Restart nginx service_
```bash
fm restart mybench --nginx
```

