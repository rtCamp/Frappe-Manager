# `fm services`

Manage the shared MariaDB server and nginx proxy every bench uses.

**Usage**:

```console
$ fm services COMMAND [ARGS]...
```

| Command | Description |
|---|---|
| [`fm services info`](#fm-services-info) | Show the global services' card: live container state, the root database credentials, the ports the proxy publishes on and which proxies in front of it are trusted. |
| [`fm services migrate`](#fm-services-migrate) | Bring fm's global services & configuration up to the current version. |
| [`fm services start`](#fm-services-start) | Start the global services shared by every bench. |
| [`fm services stop`](#fm-services-stop) | Stop the global services shared by every bench. |
| [`fm services restart`](#fm-services-restart) | Restart the global services shared by every bench. |
| [`fm services shell`](#fm-services-shell) | Open a bash shell in one of the global service containers. |
| [`fm services ports`](#fm-services-ports) | Publish the global proxy on different host ports, so fm can share a machine with another web server. |
| [`fm services prune`](#fm-services-prune) | Reclaim the host tier's disk: fm's own backup sessions and the shared services' logs. |
| [`fm services trusted-proxies`](#fm-services-trusted-proxies) | Which proxies in front of fm may speak for the client. |

## `fm services info`

Show the global services' card: live container state, the root database credentials, the ports the proxy publishes on and which proxies in front of it are trusted.

The root database password is printed in cleartext. It belongs to the mariadb container every bench shares, which is why it is on this card and not on any bench's fm info.

**Usage**:

```console
$ fm services info
```

## `fm services migrate`

Bring fm's global services & configuration up to the current version.

This is the host-wide half of a migration: the shared services every bench depends on (mariadb, nginx-proxy) and fm's own configuration. Benches are never migrated here; fm migrate refuses to run while this half is behind, so after a CLI update this command comes first.

A migration here can briefly take every bench on the host down, because the shared services are every bench's database and only route in.

**Usage**:

```console
$ fm services migrate [OPTIONS]
```

**Options**:

* `-y, --yes`: Migrate without asking for confirmation.  [default: false]
* `--dry-run`: Print the migration plan and exit without migrating; never prompts.  [default: false]
* `--skip-backup`: Skip every pre-migration backup, both kinds (DANGEROUS; prefer --skip-db-backup, which keeps the near-free config backups the rollback restores).  [default: false]
* `--skip-config-backup`: Skip the config-file backups (the services compose, fm's own config).  [default: false]
* `--skip-db-backup`: Skip whole-engine database dumps (DANGEROUS; such a dump can be the only route back from a one-way engine upgrade, so use this only when taking it is impossible).  [default: false]
* `--on-failure [prompt|rollback|halt]`: What to do when the migration fails: rollback (revert, the default), halt (leave everything as it stopped and report), prompt (ask).
* `--rerun`: Re-run the migration steps even when already up to date.  [default: false]

### Examples

#### Migrate after a CLI update

Updates the shared services (mariadb, nginx-proxy) and fm's own config. No bench version is touched; run fm migrate BENCH or fm migrate all afterwards.

```bash
fm services migrate
```

#### Migrate unattended

```bash
fm services migrate --yes
```

#### See the plan without migrating

```bash
fm services migrate --dry-run
```

#### Halt on failure for inspection

A failed cutover is left exactly as it stopped, with the backup location printed, instead of being rolled back underneath you.

```bash
fm services migrate --on-failure halt
```

## `fm services start`

Start the global services shared by every bench.

**Usage**:

```console
$ fm services start SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`  [required]

### Examples

#### Bring the global stack up

Services already running are left alone, so this is safe to re-run.

```bash
fm services start all
```

#### Start the database only

```bash
fm services start mariadb
```

## `fm services stop`

Stop the global services shared by every bench.

Every bench is reached through nginx-proxy and keeps its data in mariadb, so stopping these leaves the bench containers running but unreachable and without a database.

**Usage**:

```console
$ fm services stop SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`  [required]

### Examples

#### Take the global stack down

Services already stopped are left alone.

```bash
fm services stop all
```

## `fm services restart`

Restart the global services shared by every bench.

Every bench is reached through nginx-proxy and keeps its data in mariadb, so restarting these is a brief outage for every bench on this host. The containers are restarted in place and never recreated, so a newly pulled image or an edited compose file is not picked up.

**Usage**:

```console
$ fm services restart SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`  [required]

### Examples

#### Apply a change to the proxy

A restart is what puts a new proxy config into effect, for instance after fm services trusted-proxies set.

```bash
fm services restart nginx-proxy
```

#### Restart the whole global stack

Benches are unreachable until the proxy is back up.

```bash
fm services restart all
```

## `fm services shell`

Open a bash shell in one of the global service containers.

**Usage**:

```console
$ fm services shell SERVICE_NAME [OPTIONS]
```

**Arguments**:

* `SERVICE_NAME`: One service; all is not accepted here.  [required]

**Options**:

* `--user TEXT`: Run the shell as this user instead of the container's default.

### Examples

#### Open a shell in the global database

```bash
fm services shell mariadb
```

#### Open a shell in the proxy

```bash
fm services shell nginx-proxy
```

## `fm services ports`

Publish the global proxy on different host ports, so fm can share a machine with another web server.

Only the HOST side moves: the proxy keeps listening on 80 and 443 inside its container, because every bench resolves its own domains to that address and a site's server-side calls to itself would otherwise stop working. Redirects fm writes pick the new port up from one generated file.

On a host with no services yet this writes the setting and exits, creating nothing -- that is what makes it usable on a machine whose first install cannot get past a busy port. Where the stack already exists the ports are applied and the proxy is recreated, which is a brief outage for every bench on the host.

Let's Encrypt HTTP-01 needs port 80 reachable at the public name, so moving off 80 means using --challenge dns01, --dev or --custom for certificates fm issues.

**Usage**:

```console
$ fm services ports [OPTIONS]
```

**Options**:

* `--http INTEGER`: Host port published to the proxy's :80.
* `--https INTEGER`: Host port published to the proxy's :443.
* `--bind TEXT`: Host address to publish on, e.g. 127.0.0.1 to accept only a local front. Absent publishes on every interface.
* `-y, --yes`: Apply to a running stack without asking; the proxy is recreated.  [default: false]

### Examples

#### Move fm off a port something else already owns

Run this before the first install on a host whose 80/443 are taken: it writes the setting without creating or starting anything.

```bash
fm services ports --http 8080 --https 8443
```

#### Keep the origin private behind a local front

Only the front can then reach fm, so a forged X-Forwarded-Proto cannot arrive from anywhere else.

```bash
fm services ports --http 8080 --https 8443 --bind 127.0.0.1
```

#### Go back to the standard ports

```bash
fm services ports --http 80 --https 443
```

## `fm services prune`

Reclaim the host tier's disk: fm's own backup sessions and the shared services' logs.

Plan-first: the full plan (every path that would be touched) is printed, then one confirmation covers it; a bare Enter aborts, --yes skips the question, --dry-run stops after the plan. Covers ~/frappe/backups (migration sessions and services_<date> wholesale backups) and the shared services' log files. Retention comes from the \[prune] table in fm_config.toml; flags override for one run. fm.log is not touched: it rotates itself. The bench tier has its own command, fm prune BENCH.

**Usage**:

```console
$ fm services prune [OPTIONS]
```

**Options**:

* `--only TEXT`: Run only this category: backups or logs. Default: both.
* `--keep-backups INTEGER`: Backup sessions to keep per location instead of \[prune].keep_backup_sessions.
* `--keep-logs INTEGER`: Rotated archives to keep per log file instead of \[prune].keep_log_archives.
* `--rotate-over TEXT`: Rotate log files larger than this (e.g. '500K', '10M') instead of \[prune].rotate_logs_over.
* `-y, --yes`: Prune without asking for confirmation.  [default: false]
* `--dry-run`: Print the plan and exit without deleting anything; never prompts.  [default: false]

### Examples

#### See what a prune would remove, without removing it

```bash
fm services prune --dry-run
```

#### Reclaim host-tier disk: old backup sessions, oversized service logs

Shows the full plan (every path) first, then asks. A bare Enter aborts; type y to proceed, or pass --yes.

```bash
fm services prune
```

#### Only rotate the shared services' logs

```bash
fm services prune --only logs
```

## `fm services trusted-proxies`

Which proxies in front of fm may speak for the client.

**Usage**:

```console
$ fm services trusted-proxies COMMAND [ARGS]...
```

| Command | Description |
|---|---|
| [`fm services trusted-proxies show`](#fm-services-trusted-proxies-show) | Show which proxies this host trusts and what is read from them. |
| [`fm services trusted-proxies set`](#fm-services-trusted-proxies-set) | Trust the proxies in front of fm, so the visitor's address and scheme survive the hop. |
| [`fm services trusted-proxies clear`](#fm-services-trusted-proxies-clear) | Trust nothing in front of fm. |

### `fm services trusted-proxies show`

Show which proxies this host trusts and what is read from them.

**Usage**:

```console
$ fm services trusted-proxies show
```

#### Examples

##### See the directives actually in force

Prints the rendered configuration, not the setting that produced it, which is what a trust problem needs.

```bash
fm services trusted-proxies show
```

### `fm services trusted-proxies set`

Trust the proxies in front of fm, so the visitor's address and scheme survive the hop.

Trust only the ranges you actually sit behind: whatever you trust fully controls the client IP and the scheme that fm, your logs and frappe go on to see. Anything arriving from any other address is judged on the connection itself, so a forged header changes nothing.

Each run replaces the whole set.

**Usage**:

```console
$ fm services trusted-proxies set [OPTIONS]
```

**Options**:

* `--cdn TEXT`: Trust a CDN's published ranges. Supported: cloudflare.
* `--trust TEXT`: CIDR range or single IP of a proxy in front of fm (repeatable).
* `--client-ip-header TEXT`: Header the client IP is read from. Defaults to CF-Connecting-IP for --cdn cloudflare and X-Forwarded-For otherwise; anything that is not a valid header name is refused.

#### Examples

##### Trust Cloudflare

Proxy logs, fm maintenance --allow-ip and frappe's rate limiting then see the visitor instead of Cloudflare's edge.

```bash
fm services trusted-proxies set --cdn cloudflare
```

##### Trust your own load balancer

Each run replaces the whole set, so pass every range you sit behind in one call.

```bash
fm services trusted-proxies set --trust 203.0.113.0/24
```

##### Trust a front running on the same machine

```bash
fm services trusted-proxies set --trust 127.0.0.1
```

### `fm services trusted-proxies clear`

Trust nothing in front of fm.

Use this whenever a front is removed: leaving its range trusted lets anyone reaching fm directly claim to be any client, over any scheme.

**Usage**:

```console
$ fm services trusted-proxies clear
```

#### Examples

##### Stop trusting anything in front

Every request is then judged on the connection fm itself received, which is the right setting whenever nothing sits in front.

```bash
fm services trusted-proxies clear
```
