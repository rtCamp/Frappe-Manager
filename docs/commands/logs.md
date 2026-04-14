## `fm logs`

Show bench logs (server or container).

View logs from bench services (frappe, nginx, redis, etc.) with optional follow mode.

**Usage**:

```console
$ fm logs BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--service`: Service name (frappe, nginx, redis-cache, etc.)
* `-f, --follow`: Follow logs in real-time


## Examples

### Show frappe server logs

Displays the frappe service logs for the bench; useful to inspect server output and errors.

```bash
fm logs mybench
```

### Follow logs in real-time

Streams logs continuously; press Ctrl+C to stop following.

```bash
fm logs mybench -f
```

### Show nginx container logs

Shows the nginx container logs for the bench and follows them in real-time when -f is provided.

```bash
fm logs mybench --service nginx -f
```

### Show redis logs

Displays logs from redis-cache service; helpful when debugging caching or queuing issues.

```bash
fm logs mybench --service redis-cache
```

