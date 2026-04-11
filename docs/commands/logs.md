## `fm logs`

Show bench logs (server or container)

**Usage**:

```console
$ fm logs BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--service`: Service name (frappe, nginx, redis-cache, etc.)
* `-f, --follow`: Follow logs in real-time


**Examples**:

_Show frappe server logs_
```bash
fm logs mybench
```

_Follow logs in real-time_
```bash
fm logs mybench -f
```

_Show nginx container logs_
```bash
fm logs mybench --service nginx -f
```

_Show redis logs_
```bash
fm logs mybench --service redis-cache
```

