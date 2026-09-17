# Migrations

Updating the `fm` CLI does not update what it manages. Two commands bring the managed state up to the version of the CLI you just installed: `fm services migrate` for fm's global services & configuration, then `fm migrate` for your benches.

## Overview

FM migrates two things, tracked separately and migrated by separate commands:

1. **Global services & configuration**: the shared services (`mariadb`, `nginx-proxy`) and `~/frappe/fm_config.toml`, migrated by `fm services migrate`
2. **Benches**: each bench's `bench_config.toml`, compose files, generated nginx and supervisor config, migrated by `fm migrate BENCH` or `fm migrate all`

Both are **version-aware**: FM records the version each one is migrated to and only runs the migrations newer than that.

!!! important "The services tier is a prerequisite, never a side effect"
    `fm migrate` never migrates the global services implicitly. While they are behind it refuses outright and names the fix, because the services tier performs host-wide cutovers (v0.21.0 renames the very addresses benches dial) that must be an explicit decision:

    ```
    ⛔ fm's global services & configuration are behind (v0.20.0 < v0.21.0). Run 'fm services migrate' first.
    ```

!!! tip "After updating the CLI"
    ```bash
    fm self upgrade           # upgrade the CLI
    fm services migrate       # global services & configuration
    fm migrate all            # then the benches
    ```

---

## Every Bench Command Requires a Migrated Bench {#migration-gate}

A bench that is behind the CLI is refused, not silently used. Two gates enforce it:

- **The top-level callback**, before any subcommand runs. If the global services & configuration or the bench named on the command line are behind, it warns and asks: **Update now** (runs the migration inline, with `--auto-proceed` and `--on-failure=rollback`) or **Update later**. Choosing later exits with status 1, so the command never runs.
- **The command's own check** (`check_bench_migration_required`), carried by every command that reads or mutates a live bench. It does not prompt: it prints `Run: fm migrate <bench>` and exits 1. This catches the cases where the callback could not resolve the bench name out of `sys.argv`.

Commands that skip the callback gate entirely: `list`, `migrate`, `services migrate`, `bake`, `deploy`, `switch`, `compose`, `self update-images`.

The bench half of the callback gate is additionally skipped for `stop`, `delete`, and `maintenance`. Of those, only `stop` and `delete` carry no in-command check either, so those two are the ones you can always run against a bench you cannot migrate. `maintenance` still refuses, just without the offer to migrate inline.

!!! note "Non-interactive runs"
    Under `--non-interactive` the callback's prompt cannot be answered, so a pending migration fails the command with a message naming `fm migrate`. Migrate explicitly before the rest of a CI job.

---

## One fm at a Time Where It Matters {#locks}

fm processes on one host coordinate through lock files under `~/frappe/locks/`, using the operating system's `flock`: a hold vanishes the instant its process exits or dies (no stale locks, ever), and a refused process is told instantly instead of waiting.

- **`migration.lock`** (host-wide): every ordinary command holds it shared, meaning "don't migrate under me"; a migration holds it exclusive. So a migration refuses to start while anything else runs (`fm is busy: bake (pid 4242) is running on this host`), and every command refuses while a migration runs (`migration (pid 4242) is in progress on this host`). Day to day nothing changes: shared holds never contend with each other.
- **`bench-<name>.lock`** (per bench): the bench mutators (`switch`, `restart`, `delete`, `reset`, `update`, `create`) hold it exclusive; `fm bake BENCH` holds it shared, so a mutator cannot rewrite a bench out from under a running bake, and a second `switch` on the same bench is refused naming the first. Quick reads (`logs`, `info`, `shell`) hold nothing, and bench A's lock never affects bench B. A standalone bake (`--apps`/`--config`) touches no bench and takes no bench lock.

Observation commands (`list`, `info`, `logs`, `services info`, `ssl list`, `apps list`, `domain list`, `tools status`) hold nothing and keep working even mid-migration, which is exactly when you want to peek; for the same reason they never auto-start a stopped global stack, they report it as they find it. Beyond that, the locks cover fm-vs-fm on this host only: not other machines, and not hand-run `docker` commands.

---

## Version Tracking

**Global services & configuration**, in `~/frappe/fm_config.toml` (the on-disk key keeps its historical name):

```toml
[migration_state]
system_migrated_to = "0.20.0"
```

**Per bench**, in `~/frappe/sites/<bench>/bench_config.toml`:

```toml
[migration_state]
migrated_to = "0.20.0"
last_migration_date = "2026-04-12T14:30:45.123456"
```

A bench with no `[migration_state]` reads as version `0.0.0`.

### Minimum supported version {#minimum-supported-version}

Migrations only reach back to **v0.18.0**. From anything older, FM refuses and prints the required path:

```
Cannot migrate from v0.17.0. Minimum supported version is v0.18.0.
Migration path: v0.17.0 → v0.18.0 → v0.21.0
```

---

## Migrations on Disk {#inventory}

Three migrations ship with the current CLI. Each one runs only if the target is below its version.

| Version | Infrastructure | Per bench |
|---|---|---|
| **v0.19.0** | `nginx-proxy` image bumped to `jwilder/nginx-proxy:1.11` | the `[ssl]` table becomes a top-level `[[ssl_certificates]]` array and `preferred_challenge` becomes `challenge_type`, though nothing reads that array where it lands (see the v0.20.0 row and the warning below); nginx `SITENAME` becomes `SITE_MAPPINGS`; `alias_domains`, `upload_limit`, `restart_policy` added; runtime moves from pyenv/nvm to uv/fnm and from certbot to acme.sh; supervisor config regenerated |
| **v0.20.0** | `mariadb` moved off end-of-life `mariadb:10.6` to `mariadb:11.8`, with `MARIADB_AUTO_UPGRADE` letting the image upgrade the system tables; the global `[cloudflare]` table in `fm_config.toml` becomes the credential set labelled `cloudflare` under `[ssl.dns_providers]`, so both scopes now store labelled sets and nothing else | Adminer 4 to 5 with the FM login plugin; `admin_tools_username` / `admin_tools_password` move into the `[auth]` table; bench nginx gains the real-IP overlay and re-renders `default.conf` so it logs JSON; the `[ssl]` table is reshaped into the one form the loader reads: v0.19.0's top-level `ssl_certificates` array and `dns_providers` table move under `[ssl]`, `[ssl].dns_challenge_providers` is renamed `dns_providers`, a credential left on a certificate moves into the set labelled `cloudflare`, and the dead certificate keys (`email`, `status`, `cert_path`, `key_path`, `issued_date`, `last_renewal_attempt`, `toml_exclude`) are deleted; every bench gains a `[sites."<site>"]` entry and `[database."<site>"]` and a top-level `alias_domains` list move under it; each recorded deploy's `backup` path becomes a `backups` map keyed by site; `[switch].migrate = "auto"` becomes `true`; `default_site` is recorded in `common_site_config.json` for a bench that has none |
| **v0.21.0** | the shared services are renamed to their engine names, atomically with everything that carries the old names: compose service `global-db` becomes `mariadb` and `global-nginx-proxy` becomes `nginx-proxy` (containers `fm_mariadb`, `fm_nginx-proxy`), the shared networks become `fm-frontend-network` / `fm-backend-network` with their subnets carried over unchanged, and on macOS the data volume is copied to `fm-mariadb-data` (the old volume is kept as the rollback path and reported for manual removal) | every bench is taken down for the network swap; its compose files are rewritten to the renamed external networks and `db_host: global-db` becomes `mariadb` in every site config (external database endpoints are never touched); benches that were running are started again |

The v0.19.0 runtime rebuild is the slow part: it recreates the Python virtualenv with `uv`, reinstalls apps, and rebuilds assets.

!!! warning "v0.19.0 left TLS configuration where nothing reads it"
    v0.19.0 writes `ssl_certificates` and `dns_providers` at the **top level** of `bench_config.toml`, and no release has ever read them there: the loader only looks under `[ssl]`. A bench that stopped at v0.19.0 loads with zero certificates, and the next time fm saves the file those orphaned keys are dropped outright, taking the TLS configuration with them. v0.20.0 relocates both into `[ssl].certificates` and `[ssl].dns_providers`, so migrate before you go hunting for a certificate `fm ssl list` says is not there.

---

## Backups

Unless you skip them, backups are taken **per migration version** immediately before that version's steps run.

**Per bench**, under `~/frappe/sites/<bench>/backups/migrations/<timestamp>/<version>/`:

| What | Notes |
|---|---|
| `bench_config.toml` | |
| `docker-compose.yml` | v0.20.0 also backs up `docker-compose.admin-tools.yml`; v0.21.0 backs up all three compose files |
| `common_site_config.json`, `site_config.json` | |
| `db-<bench>-<DD-MM-YYYY--HH-MM-SS>.sql.gz` | gzipped logical dump of the bench database |
| `supervisor.conf`, `*.fm.supervisor.conf` | v0.19.0 only, because it regenerates them |
| nginx `default.conf` | v0.19.0 and v0.20.0, both of which regenerate it |

**Global services & configuration**, under `~/frappe/backups/migrations/<timestamp>/<version>/`:

| What | Notes |
|---|---|
| `docker-compose.yml` | the global services compose file |
| `global-db-all-databases-<timestamp>.sql.gz` | v0.20.0 only: whole-server dump taken while the old engine still runs, because the datadir upgrade is one-way (the file keeps the service's pre-v0.21.0 name, which is what it was called when that migration ran) |

!!! info "Timestamp format"
    `DD-Mon-YY--HH-MM-SS`, for example `12-Apr-26--14-30-45`. Collisions within one run get microseconds appended.

!!! warning "`fm_config.toml` is backed up only when a step rewrites it"
    FM rewrites the version in `fm_config.toml` in place and rewinds it on rollback, without taking a copy. A step that reshapes the file does take one: v0.20.0's DNS-credential relocation backs it up before rewriting and restores it on rollback, so look for it under that run's backup directory.

---

## Migration Commands

### Global services & configuration {#migrate-services}

```bash
fm services migrate
```

Migrates the shared services and FM's own config. No bench version is touched, though a host-wide cutover (like the v0.21.0 rename) may rewrite bench files and briefly take every bench down, because the shared services are every bench's database and only route in. If there is nothing to do it prints `Global services & configuration already at v<version>` and exits 0.

### One bench {#migrate-bench}

```bash
fm migrate mybench.localhost
```

Migrates that bench. Refused while the global services & configuration are behind: run `fm services migrate` first.

!!! info "Running benches are recreated"
    The bench does not need to be stopped first. If it is running, FM warns that its containers will be restarted (recreated) during migration. Stop it with `fm stop mybench` beforehand only if you want to pick the downtime window yourself.

!!! note "Bench names resolve like everywhere else"
    `fm migrate` takes the same `BENCH|all` address every bench command takes: completion offers your benches, a bare `mybench` finds a legacy `mybench.localhost` directory, and a name that matches nothing is refused while the argument is parsed.

### Every bench {#migrate-all}

```bash
fm migrate all
```

Targets every directory in `~/frappe/sites/` that has a `bench_config.toml`. Benches are migrated one at a time, in one pass per migration version.

Before starting, FM lists what it will do and asks once:

```
Global services & configuration: v0.19.0 → v0.20.0
  • fm configuration
  • shared services (mariadb, nginx-proxy)

Benches:
  • mybench.localhost: v0.19.0 → v0.20.0
  • prod.localhost: v0.19.0 → v0.20.0

Migration versions:
  • v0.20.0

Do you want to proceed?
  yes - Start migration
  no - Abort and revert to previous fm version
```

Answering `no` prints the `uv tool install frappe-manager==<previous>` command to get back to the CLI you came from, and exits.

`--auto-proceed` answers yes for you.

### Re-run a migration {#rerun}

```bash
fm migrate mybench.localhost --rerun
```

Re-applies the current release's migration steps even when the target is already up to date. Config transforms and supervisor regeneration run again; the runtime environment is rebuilt only when the Python or Node version actually changed or the existing environment is broken.

`--rerun` narrows discovery to the current release, so old migrations are not replayed.

### Exclude benches {#exclude-benches}

```bash
fm migrate all --exclude-bench oldbench.localhost,legacy.localhost
```

Comma-separated, and only valid with `all`, since there is nothing to exclude from a single named bench. As with the bench name itself, the excluded names have to be the exact bench directory names.

---

## Failure Handling {#on-failure}

When a bench's migration raises, FM restores that bench's backups for the failing version and undoes the version's bench-level changes before deciding what to do next. `--on-failure` picks that decision.

### `--on-failure=prompt` (default) {#on-failure-prompt}

**One bench:** asks whether to roll the bench back to its pre-migration state, or to skip the rollback and leave it as it is for manual fixing and a retry with `fm migrate <bench>`.

**`all`:** asks whether to **archive** the failed benches and keep the successful ones migrated, or to **revert the migration for every bench**.

### `--on-failure=rollback` {#on-failure-rollback}

Rolls back without asking: backups are restored, the recorded version is rewound, and the command exits non-zero. With `all` this reverts every bench, not just the failed one, and prints how to reinstall the previous CLI.

!!! tip "Use it in automation"
    This is what the inline migration gate uses, and the safe default for unattended runs of a single production bench.

### `--on-failure=archive` {#on-failure-archive}

```bash
fm migrate all --auto-proceed --on-failure=archive
```

Each failed bench is rolled back to its last successfully completed migration version and its directory is moved from `~/frappe/sites/<bench>/` to `~/frappe/archived/<bench>/`. The benches that succeeded stay migrated. FM prints which benches it archived.

!!! warning "Not available for a single bench"
    On a single-bench run FM prints `--on-failure=archive not supported for single bench migrations. Using rollback.` and then falls through to the single-bench question above: roll the bench back, or leave it as it is. Archive only makes sense when there are other benches to keep migrated.

---

## Skipping Backups

Skips are by KIND, because the two kinds cost differently and guard against different damage: the config-file backups are near-free and restore a broken migration's file state, the per-site database dumps are the slow, large half and are the route back when a migration damaged data.

### `--skip-backup` {#skip-backups}

```bash
fm migrate all --skip-backup
```

Skips every backup, both kinds.

!!! danger "No backups means no rollback"
    Rollback restores files from the backup directory. Without it, a failed migration leaves the bench where it stopped. Use this only when you have external backups, when backup creation itself is what is failing (disk space, permissions), or on disposable benches.

### `--skip-config-backup` / `--skip-db-backup` {#skip-backup-kinds}

```bash
fm migrate all --skip-db-backup
```

One kind at a time: `--skip-db-backup` keeps the cheap config-file backups and skips the dumps (the usual reason: dump size or time), `--skip-config-backup` the reverse.

`fm services migrate` takes the same three flags. There, `--skip-db-backup` covers whole-engine dumps like v0.20.0's pre-upgrade dump, where the dump is the only route back from a one-way engine upgrade: reach for it only when taking the dump is genuinely impossible, and prefer it over `--skip-backup`, which also throws away the near-free config backups the rollback restores.

!!! note "Undeterminable database name"
    If FM cannot work out a bench's database name from `site_config.json`, `bench_config.toml`, or the global service info, it asks whether to continue without a database backup. `--skip-backup` or `--skip-db-backup` answers that in advance.

---

## Restoring by Hand

Backups are grouped by the migration version that took them, so the version subdirectory is part of the path.

```bash
BENCH=mybench.localhost
BACKUP=~/frappe/sites/$BENCH/backups/migrations/12-Apr-26--14-30-45/0.20.0

fm stop $BENCH

# config files
cp "$BACKUP/bench_config.toml" ~/frappe/sites/$BENCH/

# database: the dump has to be inside the workspace, because that is the only
# part of the bench directory mounted into the containers
cp "$BACKUP"/db-*.sql.gz ~/frappe/sites/$BENCH/workspace/frappe-bench/sites/

fm start $BENCH
fm shell $BENCH -c "bench --site $BENCH restore sites/db-*.sql.gz"
```

The Frappe site name is the bench name, which is why the same variable serves both.

---

## Troubleshooting

### Backup creation fails

```bash
df -h ~/frappe
rm -rf ~/frappe/sites/mybench.localhost/backups/migrations/<old-timestamp>/
```

If space is genuinely unavailable and you have backups elsewhere, `--skip-backup` (or `--skip-db-backup`, keeping the near-free config backups) gets the migration through.

### A bench is stuck half-migrated

The rollback was skipped, or the process was killed mid-run. List the backup timestamps and restore the right one by hand (see above); the format sorts by day-of-month, not chronologically, so read the dates rather than piping through `sort`:

```bash
ls ~/frappe/sites/mybench.localhost/backups/migrations/
```

### "Already up to date" but the config looks wrong

Use `--rerun` rather than editing `[migration_state]` by hand:

```bash
fm migrate mybench.localhost --rerun
```

It re-applies the current release's steps against the bench as it is now.

---

## How Migrations Are Selected {#internals}

Migration classes are discovered from the modules in `migration_manager/migrations/` and filtered by `from_version < migration.version <= current_version`, then sorted by version.

`from_version` is the lower of FM's recorded infrastructure version and the lowest version among the targeted benches, so one run can catch a bench that is several releases behind an already-current infrastructure.

For each selected version, in ascending order:

1. If the infrastructure is behind: back up the global services compose file, then apply that version's service changes.
2. For each targeted bench, in sequence: back up, then apply that version's bench changes. A bench already at or above the version is skipped (unless `--rerun`).

Benches are never migrated in parallel. Once a bench has failed, later versions skip it instead of compounding the damage.

**See also:** [Configuration reference](configuration.md), [Architecture reference](architecture.md), [`fm migrate`](../commands/migrate.md)
