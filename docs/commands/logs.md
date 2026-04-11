# fm logs

Tail or view logs for a bench or a specific service.

Usage:

```console
$ fm logs BENCHNAME [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `--service` | Choose service: frappe, nginx, socketio, schedule, redis-cache, redis-queue, short-worker, long-worker |
| `-f, --follow` | Follow the log stream |

Examples:

```bash
# Follow the main web log
fm logs mybench --service frappe -f

# Show nginx logs
fm logs mybench --service nginx
```

!!! tip
    Use `fm logs mybench --service short-worker -f` to follow worker logs in real time.
