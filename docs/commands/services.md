## `fm services`

Services commands.

**Usage**:

```console
$ fm services [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `info`: Show the global services' card: live container state, the root database credentials and the proxy's real-ip trust.
* `migrate`: Bring fm's global services & configuration up to the current version.
* `start`: Start the global services shared by every bench.
* `stop`: Stop the global services shared by every bench.
* `restart`: Restart the global services shared by every bench.
* `shell`: Open a bash shell in one of the global service containers.
* `real-ip`: Restore the visitor's real IP at the global nginx proxy when it sits behind a CDN or load balancer.
* `prune`: Reclaim the host tier's disk: fm's own backup sessions and the shared services' logs.


### `fm services info`

Show the global services' card: live container state, the root database credentials and the proxy's real-ip trust.

The root database password is printed in cleartext. It belongs to the mariadb container every bench shares, which is why it is on this card and not on any bench's fm info.

**Usage**:

```console
$ fm services info
```


### `fm services migrate`

Bring fm's global services & configuration up to the current version.

This is the host-wide half of a migration: the shared services every bench depends on (mariadb, nginx-proxy) and fm's own configuration. Benches are never migrated here; fm migrate refuses to run while this half is behind, so after a CLI update this command comes first.

A migration here can briefly take every bench on the host down, because the shared services are every bench's database and only route in.

**Usage**:

```console
$ fm services migrate [OPTIONS]
```

**Options**:

* `--auto-proceed`: Migrate without asking for confirmation.
* `--skip-backup`: Skip every pre-migration backup, both kinds (DANGEROUS; prefer --skip-db-backup, which keeps the near-free config backups the rollback restores).
* `--skip-config-backup`: Skip the config-file backups (the services compose, fm's own config).
* `--skip-db-backup`: Skip whole-engine database dumps (DANGEROUS; such a dump can be the only route back from a one-way engine upgrade, so use this only when taking it is impossible).
* `--on-failure`: What to do when the migration fails: rollback (revert, the default), halt (leave everything as it stopped and report), prompt (ask).
* `--rerun`: Re-run the migration steps even when already up to date.


## Examples

### Migrate after a CLI update

Updates the shared services (mariadb, nginx-proxy) and fm's own config. No bench version is touched; run fm migrate BENCH or fm migrate all afterwards.

```bash
fm services migrate
```

### Migrate unattended

```bash
fm services migrate --auto-proceed
```

### Halt on failure for inspection

A failed cutover is left exactly as it stopped, with the backup location printed, instead of being rolled back underneath you.

```bash
fm services migrate --on-failure halt
```


### `fm services start`

Start the global services shared by every bench.

**Usage**:

```console
$ fm services start SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`  [required]


## Examples

### Bring the global stack up

Services already running are left alone, so this is safe to re-run.

```bash
fm services start all
```

### Start the database only

```bash
fm services start mariadb
```


### `fm services stop`

Stop the global services shared by every bench.

Every bench is reached through nginx-proxy and keeps its data in mariadb, so stopping these leaves the bench containers running but unreachable and without a database.

**Usage**:

```console
$ fm services stop SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`  [required]


## Examples

### Take the global stack down

Services already stopped are left alone.

```bash
fm services stop all
```


### `fm services restart`

Restart the global services shared by every bench.

Every bench is reached through nginx-proxy and keeps its data in mariadb, so restarting these is a brief outage for every bench on this host. The containers are restarted in place and never recreated, so a newly pulled image or an edited compose file is not picked up.

**Usage**:

```console
$ fm services restart SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`  [required]


## Examples

### Apply a change to the proxy

A restart is what puts a new proxy config into effect, for instance after fm services real-ip.

```bash
fm services restart nginx-proxy
```

### Restart the whole global stack

Benches are unreachable until the proxy is back up.

```bash
fm services restart all
```


### `fm services shell`

Open a bash shell in one of the global service containers.

**Usage**:

```console
$ fm services shell SERVICE_NAME [OPTIONS]
```

**Arguments**:

* `SERVICE_NAME`: One service; all is not accepted here.  [required]

**Options**:

* `--user`: Run the shell as this user instead of the container's default.


## Examples

### Open a shell in the global database

```bash
fm services shell mariadb
```

### Open a shell in the proxy

```bash
fm services shell nginx-proxy
```


### `fm services real-ip`

Restore the visitor's real IP at the global nginx proxy when it sits behind a CDN or load balancer.

Trust only the ranges you actually sit behind: whatever you trust fully controls the client IP that fm, your logs and frappe go on to see.

**Usage**:

```console
$ fm services real-ip [OPTIONS]
```

**Options**:

* `--cdn`: Trust a CDN's published ranges. Supported: cloudflare.
* `--trust`: CIDR range or single IP of a proxy in front of fm (repeatable).
* `--header`: Header the client IP is read from. Defaults to CF-Connecting-IP for --cdn cloudflare and X-Forwarded-For otherwise; anything that is not a valid header name is refused.
* `--off`: Remove the configuration and reload the proxy.
* `--status`: Show the active configuration. Writes nothing.


## Examples

### Trust Cloudflare

Proxy logs, fm maintenance --allow-ip and frappe's rate limiting then see the visitor instead of Cloudflare's edge.

```bash
fm services real-ip --cdn cloudflare
```

### Trust your own load balancer

Each run replaces the whole configuration, so pass every range you sit behind in one call.

```bash
fm services real-ip --trust 203.0.113.0/24
```

### Show what is trusted

```bash
fm services real-ip --status
```


### `fm services prune`

Reclaim the host tier's disk: fm's own backup sessions and the shared services' logs.

Covers ~/frappe/backups (migration sessions and services_<date> wholesale backups) and the shared services' log files. Retention comes from the \[prune] table in fm_config.toml; flags override for one run. fm.log is not touched: it rotates itself. The bench tier has its own command, fm prune BENCH.

**Usage**:

```console
$ fm services prune [OPTIONS]
```

**Options**:

* `--only`: Run only this category: backups or logs. Default: both.
* `--keep-backups`: Backup sessions to keep per location instead of \[prune].keep_backup_sessions.
* `--keep-logs`: Rotated archives to keep per log file instead of \[prune].keep_log_archives.
* `--rotate-over`: Rotate log files larger than this (e.g. '500K', '10M') instead of \[prune].rotate_logs_over.
* `--dry-run`: Report what would be pruned without deleting anything.


## Examples

### See what a prune would remove, without removing it

```bash
fm services prune --dry-run
```

### Reclaim host-tier disk: old backup sessions, oversized service logs

```bash
fm services prune
```

### Only rotate the shared services' logs

```bash
fm services prune --only logs
```

