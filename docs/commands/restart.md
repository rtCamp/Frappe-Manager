# `fm restart`

Restart bench services: web and workers by default, redis and nginx on request.

Workers drain first: fm waits up to \[workers].drain_timeout for in-flight jobs, and rather than kill a job that overruns it resumes the workers and aborts the restart before any service is touched. --no-drain skips the wait and interrupts running jobs, --force kills everything fast, and a run naming --service skips the drain as well.

Supervisor restarts need a running bench. For a stopped one use fm start, or --container to restart-and-start the containers.

**Usage**:

```console
$ fm restart BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

**Options**:

* `--web`: Restart the web tier (frappe and socketio).  [default: true]
* `--workers`: Restart the worker tier (schedule and the RQ workers).  [default: true]
* `--redis`: Restart redis too; this briefly disconnects every consumer.  [default: false]
* `--nginx`: Restart the bench nginx service, e.g. after a proxy or TLS config change.  [default: false]
* `--container`: Restart whole containers instead of supervisor processes: slower, and it starts a stopped bench.  [default: false]
* `--force`: Kill everything fast instead of restarting it gracefully. Implies --no-drain; conflicts with --drain and --rolling.  [default: false]
* `--rolling`: Zero-downtime recreate of the web tier on the current image tag; image benches only. Web-only, so it conflicts with --redis, --nginx and --no-web.  [default: false]
* `--drain/--no-drain`: Wait for in-flight RQ jobs before restarting workers, and abort the restart if they outlast \[workers].drain_timeout.  [default: true]
* `--service TEXT`: Restart only the named service (repeatable); overrides the group flags and skips the drain.

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

## See also

- [fmx: in-container services](../guides/fmx.md)
