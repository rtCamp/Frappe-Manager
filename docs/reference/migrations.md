# Migrations

Updating the `fm` CLI does not update what it manages. `fm migrate` brings FM's own configuration, the global services, and (when you name them) your benches up to the version of the CLI you just installed.

## Overview

FM migrates two things, tracked separately:

1. **FM infrastructure**: the global services (`global-db`, `global-nginx-proxy`) and `~/frappe/fm_config.toml`
2. **Benches**: each bench's `bench_config.toml`, compose files, generated nginx and supervisor config

Both are **version-aware**: FM records the version each one is migrated to and only runs the migrations newer than that.

!!! important "A bare `fm migrate` touches no bench"
    `fm migrate` with no arguments migrates only the FM infrastructure. Benches are never migrated implicitly: name one, or pass `--all-benches`.

    Naming a bench migrates the infrastructure too, if it is behind.

!!! tip "After updating the CLI"
    ```bash
    fm self update            # update the CLI
    fm migrate                # FM infrastructure only
    fm migrate --all-benches  # then the benches
    ```

---

## Every Bench Command Requires a Migrated Bench {#migration-gate}

A bench that is behind the CLI is refused, not silently used. Two gates enforce it:

- **The top-level callback**, before any subcommand runs. If the FM infrastructure or the bench named on the command line is behind, it warns and asks: **Update now** (runs the migration inline, with `--auto-proceed` and `--on-failure=rollback`) or **Update later**. Choosing later exits with status 1, so the command never runs.
- **The command's own check** (`check_bench_migration_required`), carried by every command that reads or mutates a live bench. It does not prompt: it prints `Run: fm migrate <bench>` and exits 1. This catches the cases where the callback could not resolve the bench name out of `sys.argv`.

Commands that skip the callback gate entirely: `list`, `migrate`, `bake`, `deploy`, `switch`, `self compose`, `self update-images`.

The bench half of the callback gate is additionally skipped for `stop`, `delete`, and `maintenance`. Of those, only `stop` and `delete` carry no in-command check either, so those two are the ones you can always run against a bench you cannot migrate. `maintenance` still refuses, just without the offer to migrate inline.

!!! note "Non-interactive runs"
    Under `--non-interactive` the callback's prompt cannot be answered, so a pending migration fails the command with a message naming `fm migrate`. Migrate explicitly before the rest of a CI job.

---

## Version Tracking

**FM infrastructure**, in `~/frappe/fm_config.toml`:

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
Migration path: v0.17.0 → v0.18.0 → v0.20.0
```

---

## Migrations on Disk {#inventory}

Two migrations ship with the current CLI. Each one runs only if the target is below its version.

| Version | Infrastructure | Per bench |
|---|---|---|
| **v0.19.0** | `global-nginx-proxy` image bumped to `jwilder/nginx-proxy:1.11` | the `[ssl]` table becomes a top-level `[[ssl_certificates]]` array and `preferred_challenge` becomes `challenge_type`, though nothing reads that array where it lands (see the v0.20.0 row and the warning below); nginx `SITENAME` becomes `SITE_MAPPINGS`; `alias_domains`, `upload_limit`, `restart_policy` added; runtime moves from pyenv/nvm to uv/fnm and from certbot to acme.sh; supervisor config regenerated |
| **v0.20.0** | `global-db` moved off end-of-life `mariadb:10.6` to `mariadb:11.8`, with `MARIADB_AUTO_UPGRADE` letting the image upgrade the system tables; the global `[cloudflare]` table in `fm_config.toml` becomes the credential set labelled `cloudflare` under `[ssl.dns_providers]`, so both scopes now store labelled sets and nothing else | Adminer 4 to 5 with the FM login plugin; `admin_tools_username` / `admin_tools_password` move into the `[auth]` table; bench nginx gains the real-IP overlay and re-renders `default.conf` so it logs JSON; the `[ssl]` table is reshaped into the one form the loader reads: v0.19.0's top-level `ssl_certificates` array and `dns_providers` table move under `[ssl]`, `[ssl].dns_challenge_providers` is renamed `dns_providers`, a credential left on a certificate moves into the set labelled `cloudflare`, and the dead certificate keys (`email`, `status`, `cert_path`, `key_path`, `issued_date`, `last_renewal_attempt`, `toml_exclude`) are deleted |

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
| `docker-compose.yml` | v0.20.0 also backs up `docker-compose.admin-tools.yml` |
| `common_site_config.json`, `site_config.json` | |
| `db-<bench>-<DD-MM-YYYY--HH-MM-SS>.sql.gz` | gzipped logical dump of the bench database |
| `supervisor.conf`, `*.fm.supervisor.conf` | v0.19.0 only, because it regenerates them |
| nginx `default.conf` | v0.19.0 and v0.20.0, both of which regenerate it |

**FM infrastructure**, under `~/frappe/backups/migrations/<timestamp>/<version>/`:

| What | Notes |
|---|---|
| `docker-compose.yml` | the global services compose file |
| `global-db-all-databases-<timestamp>.sql.gz` | v0.20.0 only: whole-server dump taken while the old engine still runs, because the datadir upgrade is one-way |

!!! info "Timestamp format"
    `DD-Mon-YY--HH-MM-SS`, for example `12-Apr-26--14-30-45`. Collisions within one run get microseconds appended.

!!! warning "`fm_config.toml` is not backed up"
    FM rewrites the version in `fm_config.toml` in place and rewinds it on rollback. There is no copy in the backup directory.

---

## Migration Commands

### Infrastructure only {#migrate-infrastructure}

```bash
fm migrate
```

Migrates the global services and FM's own config. Nothing bench-specific happens. If there is nothing to do it prints `FM infrastructure already up to date (no benches specified)` and exits 0.

### One bench {#migrate-bench}

```bash
fm migrate mybench.localhost
```

Migrates that bench, plus the infrastructure if it is behind.

!!! info "Running benches are recreated"
    The bench does not need to be stopped first. If it is running, FM warns that its containers will be restarted (recreated) during migration. Stop it with `fm stop mybench` beforehand only if you want to pick the downtime window yourself.

!!! warning "`fm migrate` wants the exact directory name"
    Most commands normalise a bare `mybench` to `mybench.localhost` for you. `fm migrate` does not: it looks up `~/frappe/sites/<what you typed>` and reports `Bench 'mybench' does not exist` if that is not a directory. Use the name `fm list` shows.

### Every bench {#migrate-all-benches}

```bash
fm migrate --all-benches
```

Targets every directory in `~/frappe/sites/` that has a `bench_config.toml`. Benches are migrated one at a time, in one pass per migration version.

Before starting, FM lists what it will do and asks once:

```
FM Infrastructure: v0.19.0 → v0.20.0
  • CLI configuration
  • Global services (MariaDB, Nginx-Proxy)

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
fm migrate --all-benches --exclude-bench oldbench.localhost,legacy.localhost
```

Comma-separated, and only valid with `--all-benches`. As with the positional argument, the names have to be the exact bench directory names.

---

## Failure Handling {#on-failure}

When a bench's migration raises, FM restores that bench's backups for the failing version and undoes the version's bench-level changes before deciding what to do next. `--on-failure` picks that decision.

### `--on-failure=prompt` (default) {#on-failure-prompt}

**One bench:** asks whether to roll the bench back to its pre-migration state, or to skip the rollback and leave it as it is for manual fixing and a retry with `fm migrate <bench>`.

**`--all-benches`:** asks whether to **archive** the failed benches and keep the successful ones migrated, or to **revert the migration for every bench**.

### `--on-failure=rollback` {#on-failure-rollback}

Rolls back without asking: backups are restored, the recorded version is rewound, and the command exits non-zero. With `--all-benches` this reverts every bench, not just the failed one, and prints how to reinstall the previous CLI.

!!! tip "Use it in automation"
    This is what the inline migration gate uses, and the safe default for unattended runs of a single production bench.

### `--on-failure=archive` {#on-failure-archive}

```bash
fm migrate --all-benches --auto-proceed --on-failure=archive
```

Each failed bench is rolled back to its last successfully completed migration version and its directory is moved from `~/frappe/sites/<bench>/` to `~/frappe/archived/<bench>/`. The benches that succeeded stay migrated. FM prints which benches it archived.

!!! warning "Not available for a single bench"
    On a single-bench run FM prints `--on-failure=archive not supported for single bench migrations. Using rollback.` and then falls through to the single-bench question above: roll the bench back, or leave it as it is. Archive only makes sense when there are other benches to keep migrated.

---

## Skipping Backups

### `--skip-all-backup` {#skip-backups}

```bash
fm migrate --all-benches --skip-all-backup
```

!!! danger "No backups means no rollback"
    Rollback restores files from the backup directory. Without it, a failed migration leaves the bench where it stopped. Use this only when you have external backups, when backup creation itself is what is failing (disk space, permissions), or on disposable benches.

### `--skip-backup-for` {#skip-backup-for}

```bash
fm migrate --all-benches --skip-backup-for testbench.localhost,devbench.localhost
```

Comma-separated. Those benches are migrated without a backup; the rest are backed up normally.

!!! note "Undeterminable database name"
    If FM cannot work out a bench's database name from `site_config.json`, `bench_config.toml`, or the global service info, it asks whether to continue without a database backup. `--skip-all-backup` or `--skip-backup-for <bench>` answers that in advance.

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

If space is genuinely unavailable and you have backups elsewhere, `--skip-all-backup` gets the migration through.

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
