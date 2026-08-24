# Logs & Debugging

Three kinds of log: what the `fm` CLI itself did, what a bench's Frappe processes wrote to disk, and what a container printed to stdout.

## Log Locations

| What | Where |
|---|---|
| **CLI operations** | `~/frappe/logs/fm.log` |
| **Bench Frappe processes** | `~/frappe/sites/<bench>/workspace/frappe-bench/logs/` |
| **Bench nginx** | `~/frappe/sites/<bench>/configs/nginx/logs/` (`access.log`, `error.log`) |
| **Global MariaDB** | `~/frappe/services/mariadb/logs/` |
| **Global nginx proxy** | `~/frappe/services/nginx-proxy/logs/` |
| **Any container** | `fm logs <bench> --service <service>` |

Redis is per bench, not a global service, so it has no directory under `~/frappe/services/`. Its output is container output only: `fm logs <bench> --service redis-cache`.

---

## `fm.log` {#fm-log}

Everything every `fm` invocation did, at `DEBUG` by default. This is the file to reach for when a command failed and the console output was not enough.

```bash
tail -f ~/frappe/logs/fm.log
```

### Line format {#fm-log-format}

```
[<timestamp>] <LEVEL>: [corr=<id>] [bench=<name>] [op=<operation>] [component=<component>] <message>
```

```
[2026-08-24 13:15:43,413] DEBUG: [corr=a8c2a3de] [op=info] [component=output] [OUTPUT] STOP
```

Every record is stamped with the ambient context at emit time, so the tags appear on lines from any subsystem, including worker threads:

- `corr=`: first 8 characters of a UUID generated once per invocation
- `bench=` / `op=`: the bench and the command the line belongs to, when known
- `component=`: the subsystem that logged it (`docker`, `migration`, `output`, `main`, ...)

```bash
# Everything one command did, in order
grep 'corr=a8c2a3de' ~/frappe/logs/fm.log
```

Lines tagged `[component=output] [OUTPUT]` are the mirror of what was printed to your terminal, so the file is a superset of the console.

### Rotation {#fm-log-rotation}

Rotates at 10 MB, keeping three gzipped backups: `fm.log.1.gz`, `fm.log.2.gz`, `fm.log.3.gz`. There is no way to turn this off.

### File log level {#file-level}

`~/frappe/fm_config.toml`:

```toml
[logs]
file_level = "DEBUG"
```

Accepts `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Anything else raises a configuration error listing the valid names. This setting applies to `fm.log` only; console verbosity is a separate flag (see below).

**See also:** [`logs.file_level`](configuration.md#logs-file-level)

---

## `fm logs`

```bash
# The bench's web server log files
fm logs mybench

# Stream them
fm logs mybench -f

# A container's docker logs instead
fm logs mybench --service nginx -f
```

Without `--service`, `fm logs` reads the bench's log **files** from the host, so it works whether or not the bench is running:

- **dev**: `web.dev.log`
- **prod**: `web.error.log`, then `web.log`

Existing content is printed file by file, then `-f` polls all of them for new lines. Files that do not exist yet are skipped; if none exist you get `No log files found.`

With `--service`, the logs come from docker and that container has to be running, otherwise `fm logs` reports that the service is not running and prints nothing.

`--service` accepts any service in the bench's compose files: `docker-compose.yml` (`frappe`, `nginx`, `socketio`, `schedule`, `redis-cache`, `redis-queue`), `docker-compose.workers.yml` (`short-worker`, `long-worker`, `default-worker`, plus one per custom queue), and `docker-compose.admin-tools.yml` (`adminer`, `mailpit`) when admin tools are enabled. An unknown name prints the bench's actual list.

---

## Bench Log Files

Written by supervisor inside the container to `~/frappe/sites/<bench>/workspace/frappe-bench/logs/`:

| File | Environment | Content |
|---|---|---|
| `web.dev.log` | dev | Dev server, stdout and stderr combined |
| `watch.dev.log` | dev | Asset watcher |
| `web.log` / `web.error.log` | prod | Gunicorn stdout / stderr |
| `worker.log` / `worker.error.log` | both | Every background worker (short, long, default, custom) writes to the same pair |
| `schedule.log` / `schedule.error.log` | both | Scheduler |
| `node-socketio.log` / `node-socketio.error.log` | both | Socket.IO server |

```bash
tail -f ~/frappe/sites/mybench.localhost/workspace/frappe-bench/logs/worker.error.log
```

---

## Bench nginx Access Log {#nginx-access-log}

Bench nginx writes a structured JSON access log, in the same format as the global nginx proxy, so one parser handles both hops.

`~/frappe/sites/<bench>/configs/nginx/logs/access.log`:

```json
{"time":"2026-08-24T13:15:43+00:00","request_id":"7f3c...","client":"203.0.113.7","xff":"203.0.113.7","host":"mybench.localhost","scheme":"https","method":"GET","path":"/app/todo","status":200,"bytes":18244,"request_time":0.212,"upstream":"10.0.5.3:80","upstream_status":"200","upstream_time":"0.208","referer":"-","ua":"Mozilla/5.0 ..."}
```

`status`, `bytes` and `request_time` are numbers. The `upstream*` fields are strings because nginx writes `-` there when there was no upstream (a maintenance-page 503, for example) and a comma-separated list when it retried.

`client` is the visitor's address, not the proxy's: a `real-ip.conf` overlay tells bench nginx to trust `X-Real-IP` from the FM frontend network. It is re-materialised on every `fm start`.

```bash
# Slowest requests
jq -sr 'sort_by(-.request_time) | .[:20] | .[] | "\(.request_time) \(.status) \(.path)"' \
  ~/frappe/sites/mybench.localhost/configs/nginx/logs/access.log

# Error rate by path
jq -r 'select(.status >= 500) | .path' \
  ~/frappe/sites/mybench.localhost/configs/nginx/logs/access.log | sort | uniq -c | sort -rn
```

!!! note "Older benches"
    The JSON format arrived with the v0.20.0 migration, which deletes the generated `conf.d/default.conf` so the container re-renders it from the current image template. A bench that has not been migrated still writes nginx's combined format.

---

## Console Verbosity Flags

These are options on `fm` itself, so they go before the subcommand.

### `--verbose` / `-v`

Turns on console log output at `INFO`.

```bash
fm -v start mybench
```

### `--log-level`

```bash
fm --log-level debug create mybench
```

Accepts `debug`, `info`, `warning` and `error`; anything else exits 1 with the valid list.

!!! note "Only `debug` and `info` add console output"
    Console log output is attached only when the level is `INFO` or `DEBUG` (or `-v` was passed). `--log-level warning` and `--log-level error` therefore leave the console log handler off entirely, which is also the default. Command output, tables and prompts are unaffected either way; `fm.log` still records everything at `logs.file_level`.

### `--non-interactive` / `-n`

Fails instead of prompting, naming the flag that would have supplied the answer. Use it in CI.

```bash
fm -n create mybench --apps erpnext --environment prod
```

### `--version` / `-V`

Prints the version and nothing else:

```console
$ fm --version
0.20.0
```

---

## Rotating Bench Logs

Frappe does not rotate its own logs, and neither does FM. On long-lived hosts, hand them to logrotate:

```
# /etc/logrotate.d/frappe-manager
/home/user/frappe/sites/*/workspace/frappe-bench/logs/*.log
/home/user/frappe/sites/*/configs/nginx/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

`copytruncate` matters here: the processes hold their log files open and will not reopen them, so renaming out from under them would silently stop the logging.

**See also:** [Configuration reference](configuration.md), [Architecture reference](architecture.md)
