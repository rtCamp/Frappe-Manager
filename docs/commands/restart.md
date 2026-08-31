## `fm restart`

Restart bench services: web and workers by default, redis and nginx on request.

Workers drain first: fm waits for in-flight jobs and aborts the restart rather than kill a job that does not finish in time. --no-drain skips the wait and interrupts running jobs; --force kills everything fast.

Supervisor restarts need a running bench. For a stopped one use fm start, or --container to restart-and-start the containers.

**Usage**:

```console
$ fm restart BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--web`: Restart the web tier (frappe and socketio).
* `--workers`: Restart the worker tier (schedule and the RQ workers).
* `--redis`: Restart redis too; this briefly disconnects every consumer.
* `--nginx`: Restart the bench nginx service, e.g. after a proxy or TLS config change.
* `--container`: Restart whole containers instead of supervisor processes: slower, and it starts a stopped bench.
* `--force`: Kill everything fast instead of restarting it gracefully. Implies --no-drain; conflicts with --drain and --rolling.
* `--rolling`: Zero-downtime recreate of the web tier on the current image tag; image benches only. Web-only, so it conflicts with --redis, --nginx and --no-web.
* `--drain/--no-drain`: Wait for in-flight RQ jobs before restarting workers, and abort the restart if they outlast \[workers].drain_timeout.
* `--service`: Restart only the named service (repeatable); overrides the group flags and skips the drain.


## Examples

### Restart web and workers

```bash
fm restart mybench
```

### Restart workers only

```bash
fm restart mybench --workers --no-web
```

### Restart without waiting for in-flight jobs

Interrupted jobs land in the failed-jobs registry.

```bash
fm restart mybench --no-drain
```

### Restart one service

Repeatable, and it skips the drain.

```bash
fm restart mybench --service socketio
```

### Zero-downtime web restart

```bash
fm restart mybench --rolling
```

## Related

- [fmx: In-Container Service Manager](../guides/fmx.md)
