# Logs & Debugging

FM provides multiple log streams for debugging bench issues, monitoring service health, and tracking CLI operations.

## Overview

Logs are separated into three layers:

1. **CLI logs** — FM command operations (`fm create`, `fm update`, etc.)
2. **Bench logs** — Frappe application logs (web server, workers, scheduler)
3. **Service logs** — Global infrastructure (MariaDB, Redis, nginx-proxy)

!!! tip "Quick access"
    **Stream live bench logs:**
    ```bash
    fm logs mybench --follow
    ```
    
    **View specific service:**
    ```bash
    fm logs mybench --service frappe --follow
    ```
    
    **Check CLI operation history:**
    ```bash
    tail -f ~/frappe/logs/fm.log
    ```

---

## Log Locations

| Log Type | Location | Description |
|---|---|---|
| **CLI operations** | `~/frappe/logs/fm.log` | All `fm` command output (auto-rotated) |
| **Bench web server** | `~/frappe/sites/<bench>/workspace/frappe-bench/logs/` | Frappe web logs (`web.log`, `web.error.log`, `web.dev.log`) |
| **Bench workers** | Same as bench web server | Worker logs (`worker.log`, `worker.error.log`) |
| **Bench scheduler** | Same as bench web server | Scheduled job logs (`schedule.log`) |
| **Global MariaDB** | `~/frappe/services/global-db/logs/` | Database server logs |
| **Global Redis** | `~/frappe/services/global-redis-*/logs/` | Redis server logs |
| **nginx-proxy** | Container logs (Docker) | HTTP access logs, SSL validation logs |

!!! info "CLI log rotation"
    `fm.log` rotates automatically when it exceeds 10MB. Older files: `fm.log.1`, `fm.log.2`, etc. (up to 5 backups).

---

## Viewing Logs with `fm logs`

### Basic Usage

```bash
# View all bench container logs (combined)
fm logs mybench

# Stream live (follow mode)
fm logs mybench --follow

# View specific service
fm logs mybench --service frappe

# Limit to last 50 lines
fm logs mybench --tail 50
```

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
fm logs mybench --service frappe --tail 100

# Monitor worker job processing
fm logs mybench --service short-worker --follow

# Check nginx routing issues
fm logs mybench --service nginx --tail 50

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

Enable info-level verbose output (shows additional operation details).

```bash
fm --verbose create mybench
fm -v start mybench
```

### `--log-level` {#log-level-flag}

Override log level explicitly.

```bash
# Debug level (most verbose)
fm --log-level debug create mybench

# Warning level only (minimal output)
fm --log-level warning restart mybench
```

**Valid levels:** `debug`, `info`, `warning`, `error`, `critical`

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
    `file_level` only affects `~/frappe/logs/fm.log`. Console output verbosity is controlled by `--verbose` / `--log-level` flags.

**See also:** [Configuration reference — logs.file_level](/reference/configuration/#logs-file-level)

---

## Log Rotation

### CLI Logs (`fm.log`)

**Automatic rotation:**

- Max size: 10 MB per file
- Backup count: 5 files (`fm.log.1` through `fm.log.5`)
- Rotation trigger: On next write after exceeding 10 MB

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
    create 0644 user user
    sharedscripts
    postrotate
        # Restart benches to reopen log files
        /usr/local/bin/fm restart --all > /dev/null 2>&1 || true
    endscript
}
```

**See also:** [logrotate documentation](https://linux.die.net/man/8/logrotate)
