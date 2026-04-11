# fm start

Start a bench. This ensures the bench services (web, workers, redis, nginx) are running.

Usage:

```console
$ fm start BENCHNAME [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `-f, --force` | Recreate containers if needed |
| `--sync-config` | Sync configuration files |
| `--reconfigure-supervisor` | Rebuild supervisor config |
| `--reconfigure-workers` | Reconfigure and start workers |
| `--include-default-workers` | Include default worker set |

Example:

```bash
fm start mybench
```

If you want to start global services (global-db and nginx-proxy) use `fm services start all`.
