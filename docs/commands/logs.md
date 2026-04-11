# fm logs

Tail or view logs for a bench or a specific service.

Usage:

```console
$ fm logs BENCHNAME [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `--service` | Choose service: frappe, nginx, redis-cache, etc. |
| `-f, --follow` | Follow the log stream |

Examples:

```bash
# Follow the main web log
fm logs mybench --service frappe -f

# Show nginx logs
fm logs mybench --service nginx
```
