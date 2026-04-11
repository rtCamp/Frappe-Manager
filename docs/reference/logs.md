# Logs & Debugging

## Where logs live

- CLI operation log: `~/frappe/logs/fm.log`
- Bench logs (web / dev server): `~/frappe/sites/<bench>/workspace/frappe-bench/logs/`
- Global service logs: `~/frappe/services/<service>/logs`

CLI logs rotate automatically. Older files appear as `fm.log.1`, `fm.log.2`, etc.

## Viewing bench logs with the CLI

```bash
# Stream logs from the frappe container
fm logs mybench --service frappe -f

# View logs from nginx
fm logs mybench --service nginx

# View logs from a worker
fm logs mybench --service short-worker
```

Service names accepted by `fm logs --service` and `fm shell --service`:

`frappe`, `nginx`, `socketio`, `schedule`, `redis-cache`, `redis-queue`, `short-worker`, `long-worker`, `adminer`, `mailpit`

!!! note "NON_BASH services"
    `redis-cache` and `redis-queue` do not have bash. When opening a shell into these services with `fm shell --service redis-cache`, FM automatically uses `sh` instead of bash.

## Global CLI flags for verbosity

These flags apply to every `fm` command:

```bash
# Enable info-level verbose output
fm --verbose info mybench

# Set log level explicitly
fm --log-level debug info mybench
fm --log-level warning create mybench
```

| Flag | Description |
|---|---|
| `-v, --verbose` | Enable info-level verbose output |
| `--log-level` | Override log level: `debug`, `info`, `warning`, `error` |
| `-n, --non-interactive` | Disable interactive prompts; all prompts print an error and suggest the required flag instead |
| `-V, --version` | Print the installed FM version and exit |

The `--non-interactive` flag is useful for CI/CD pipelines where no TTY is available:

```bash
fm --non-interactive create mybench --apps erpnext
```
