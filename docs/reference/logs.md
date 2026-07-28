# Logs & Debugging

FM provides multiple log streams for debugging bench issues, monitoring service health, and tracking CLI operations.

## Overview

Logs are separated into three layers:

1. **CLI logs** - FM command operations (`fm create`, `fm update`, etc.)
2. **Bench logs** - Frappe application logs (web server, workers, scheduler)
3. **Service logs** - Global infrastructure (MariaDB, Redis, nginx-proxy)

!!! tip "Quick access"
    **Stream the bench web server log:**
    ```bash
    fm logs mybench --follow
    ```
    
    **View a specific container:**
    ```bash
    fm logs mybench --service frappe --follow
    ```
    
    **Check FM's own operation log:**
    ```bash
    tail -f ~/frappe/logs/fm.log
    ```

---

## Log Locations

| Log Type | Location | Description |
|---|---|---|
| **CLI operations** | `~/frappe/logs/fm.log` | Everything every `fm` command did (auto-rotated, gzipped backups) |
| **Bench web server** | `~/frappe/sites/<bench>/workspace/frappe-bench/logs/` | Frappe web logs (`web.log`, `web.error.log`, `web.dev.log`) |
| **Bench workers** | Same as bench web server | Worker logs (`worker.log`, `worker.error.log`) |
| **Bench scheduler** | Same as bench web server | Scheduled job logs (`schedule.log`) |
| **Global MariaDB** | `~/frappe/services/global-db/logs/` | Database server logs |
| **Global Redis** | `~/frappe/services/global-redis-*/logs/` | Redis server logs |
| **nginx-proxy** | Container logs (Docker) | HTTP access logs, SSL validation logs |

!!! info "CLI log rotation"
    `fm.log` rotates automatically when it exceeds 10MB. Rotated files are gzipped: `fm.log.1.gz`, `fm.log.2.gz`, `fm.log.3.gz` (3 backups kept).


### `fm.log` Line Format {#fm-log-format}

Every line in `fm.log` carries ambient context - a correlation id for the CLI invocation, plus the bench, operation, and component when known:

```
[<timestamp>] LEVEL: [corr=<id>] [bench=<name>] [op=<operation>] [component=<component>] message
```

Example:

```
[2026-07-27 17:41:12,694] INFO: [corr=5211fa23] [op=list] [component=output] [OUTPUT] DATA:
```

- `corr=` - first 8 chars of a per-invocation correlation id; grep it to see everything one command did
- `bench=` / `op=` - the bench and operation the line belongs to (present when applicable)
- `component=` - which subsystem logged it (e.g. `docker`, `migration`, `output`)

```bash
# Trace a single fm invocation end to end
grep 'corr=5211fa23' ~/frappe/logs/fm.log
```
---

## Viewing Logs with `fm logs`

`fm logs` shows **bench** logs - not FM's own operation log (that lives in `~/frappe/logs/fm.log`).

### Basic Usage

```bash
# View the bench web server log
# (web.dev.log in dev; web.log + web.error.log in prod)
fm logs mybench

# Stream live (follow mode)
fm logs mybench --follow

# View a specific container's logs instead
fm logs mybench --service frappe
```

Without `--service`, `fm logs` reads the Frappe web server **file** logs from the bench workspace. With `--service`, it shows that container's Docker logs (the service must be running).

### Available Services {#available-services}

| Service | Description |
|---|---|
| `frappe` | Main Frappe web server (Gunicorn or Werkzeug) |
| `nginx` | Per-bench nginx container (reverse proxy) |
| `socketio` | Real-time WebSocket server |
| `schedule` | Scheduled task worker (cron-like) |
| `redis-cache` | Per-bench Redis cache instance |
| `redis-queue` | Per-bench Redis queue instance |
| `short-worker` | Short background job worker |
| `long-worker` | Long background job worker |
| `adminer` | Database admin UI (if admin tools enabled) |
| `mailpit` | Email testing UI (if admin tools enabled) |

!!! warning "Redis services use `sh` shell"
    `redis-cache` and `redis-queue` containers do not have bash. When using `fm shell --service redis-cache`, FM automatically falls back to `sh`.

### Examples

```bash
# Debug web server errors
fm logs mybench --service frappe

# Monitor worker job processing
fm logs mybench --service short-worker --follow

# Check nginx routing issues
fm logs mybench --service nginx

# View scheduler execution
fm logs mybench --service schedule --follow
```

---

## Bench Application Logs

Frappe writes application logs to `~/frappe/sites/<bench>/workspace/frappe-bench/logs/`:

| Log File | Environment | Content |
|---|---|---|
| `web.dev.log` | Dev | All web server output (stdout + stderr combined) |
| `web.log` | Prod | Web server stdout (successful requests, info messages) |
| `web.error.log` | Prod | Web server stderr (errors, warnings, tracebacks) |
| `worker.log` | Both | Worker stdout (job processing logs) |
| `worker.error.log` | Both | Worker errors and failed jobs |
| `schedule.log` | Both | Scheduled task execution logs |

!!! tip "Environment-specific log splitting"
    **Dev environment:** Single `web.dev.log` file (easier tailing)  
    **Prod environment:** Separate `web.log` and `web.error.log` (easier filtering)

### Reading Application Logs Directly

```bash
# Stream web server logs
tail -f ~/frappe/sites/mybench/workspace/frappe-bench/logs/web.log

# View recent errors only (prod)
tail -n 100 ~/frappe/sites/mybench/workspace/frappe-bench/logs/web.error.log

# Monitor worker job failures
tail -f ~/frappe/sites/mybench/workspace/frappe-bench/logs/worker.error.log
```

---

## CLI Verbosity Flags

Control CLI output verbosity globally (applies to all `fm` commands):

### `--verbose` / `-v` {#verbose-flag}

Sets console output to `INFO` level (shows additional operation details). Default console level is `WARNING`.

```bash
fm --verbose create mybench
fm -v start mybench
```

### `--log-level` {#log-level-flag}

Override the console log level explicitly (takes precedence over `--verbose`).

```bash
# Debug level (most verbose)
fm --log-level debug create mybench

# Warning level only (minimal output)
fm --log-level warning restart mybench
```

**Valid levels:** `debug`, `info`, `warning`, `error`

### `--non-interactive` / `-n` {#non-interactive-flag}

Disable interactive prompts (useful for CI/CD, automation).

```bash
# Will error if required options missing (no prompts)
fm --non-interactive create mybench --apps erpnext
```

!!! tip "CI/CD usage"
    Always use `--non-interactive` in automation to prevent hanging on prompts:
    
    ```bash
    fm --non-interactive create mybench --apps erpnext --environment prod
    ```

### `--version` / `-V` {#version-flag}

Print installed FM version and exit.

```bash
fm --version
# Output: frappe-manager 0.19.0
```

---

## Global CLI Log Configuration

Configure CLI log verbosity in `~/frappe/fm_config.toml`:

```toml
[logs]
file_level = "DEBUG"
```

**Valid levels:** `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

!!! info "File log vs console output"
    `file_level` only affects `~/frappe/logs/fm.log` (default `DEBUG`). Console output verbosity is controlled by the `--verbose` / `--log-level` flags.

**See also:** [Configuration reference - logs.file_level](/reference/configuration/#logs-file-level)

---

## Log Rotation

### CLI Logs (`fm.log`)

**Automatic rotation:**

- Max size: 10 MB per file
- Backup count: 3 gzipped files (`fm.log.1.gz` through `fm.log.3.gz`)
- Rotation trigger: On next write after exceeding 10 MB; the rotated file is gzip-compressed

**Disable rotation:** Not supported (prevents disk exhaustion).

### Application Logs (Frappe)

Frappe does not auto-rotate logs. For production deployments, configure logrotate:

```bash
# /etc/logrotate.d/frappe-manager
/home/user/frappe/sites/*/workspace/frappe-bench/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

`copytruncate` avoids having to restart the bench processes to reopen log files.

**See also:** [logrotate documentation](https://linux.die.net/man/8/logrotate)
