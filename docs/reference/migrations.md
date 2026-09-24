# Migrations

Updating the `fm` CLI does not update what it manages. Two commands bring the managed state up to the version of the CLI you just installed: `fm services migrate` for fm's global services & configuration, then `fm migrate` for your benches.

## Overview

FM migrates two things, tracked separately and migrated by separate commands:

1. **Global services & configuration**: the shared services (`mariadb`, `nginx-proxy`) and `~/frappe/fm_config.toml`, migrated by `fm services migrate`
2. **Benches**: each bench's `bench_config.toml`, compose files, generated nginx and supervisor config, migrated by `fm migrate BENCH` or `fm migrate all`

Both are **version-aware**: FM records the version each one is migrated to and only runs the migrations newer than that. What each shipped migration actually changed is catalogued in the [Migration History](migration-history.md).

!!! important "The services tier is a prerequisite, never a side effect"
    `fm migrate` never migrates the global services implicitly. While they are behind it refuses outright and names the fix, because the services tier performs host-wide cutovers (v1.0.0 renames the very addresses benches dial) that must be an explicit decision:

    ```
    ⛔ fm's global services & configuration are behind (v0.19.0 < v1.0.0). Run 'fm services migrate' first.
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

- **The top-level callback**, before any subcommand runs. If the global services & configuration or the bench named on the command line are behind, it warns and asks: **Update now** (runs the migration inline, unattended, with `--on-failure=rollback`) or **Update later**. Choosing later exits with status 1, so the command never runs.
- **The command's own check** (`check_bench_migration_required`), carried by every command that reads or mutates a live bench. It does not prompt: it prints `Run: fm migrate <bench>` and exits 1. This catches the cases where the callback could not resolve the bench name out of `sys.argv`.

Commands that skip the callback gate entirely: `list`, `compose`, `self update-images`, `migrate`, `services migrate`, `bake`, `switch`, `prune`, and `services prune`. The two prune commands are on that list deliberately: a full disk is exactly the situation where a migration cannot run, and pruning only reads the tree and deletes files the migrations do not manage.

The bench half of the callback gate is additionally skipped for `stop`, `delete`, and `maintenance`. Of those, only `stop` and `delete` carry no in-command check either, so those two are the ones you can always run against a bench you cannot migrate. `maintenance` still refuses, just without the offer to migrate inline.

While a migration runs, every other fm command on the host is refused (and a migration refuses to start while anything else runs); observation commands like `fm list` stay usable throughout. See [Process Locks](locks.md).

!!! note "Non-interactive runs"
    Under `--non-interactive` the callback's prompt cannot be answered, so a pending migration fails the command with a message naming `fm migrate`. Migrate explicitly before the rest of a CI job. Running `fm migrate` or `fm services migrate` itself under `--non-interactive` without `--yes` is refused the same way, naming `--yes` instead -- `--dry-run` is the one form of either command that never prompts and always exits 0, so it works non-interactively with no flag needed.

---

## Version Tracking

**Global services & configuration**, in `~/frappe/fm_config.toml` (files from older releases carry the key as `system_migrated_to`; it is renamed on the next migration):

```toml
[migration_state]
migrated_to = "1.0.0"
```

**Per bench**, in `~/frappe/sites/<bench>/bench_config.toml`:

```toml
[migration_state]
migrated_to = "1.0.0"
last_migration_date = "2026-04-12T14:30:45.123456"
```

### Unknown versions refuse {#unknown-version}

A missing `[migration_state]` or an unparseable `migrated_to` reads as *unknown* (`0.0.0`). Observation commands still work on such a bench, but **migrating it is refused**, naming the file: fm will not guess what a system is migrated to, because guessing wrong means re-running every migration against a state that may already be current. If you know the real version (say, a bench restored from a partial backup), write it back by hand and re-run:

```toml
[migration_state]
migrated_to = "1.0.0"
```

### Minimum supported version {#minimum-supported-version}

Migrations only reach back to **v0.18.0**. From anything older, FM refuses and prints the required path:

```
Cannot migrate from v0.17.0. Minimum supported version is v0.18.0.
Migration path: v0.17.0 → v0.18.0 → v1.0.0
```

---

## Running Migrations

### Global services & configuration {#migrate-services}

```bash
fm services migrate
```

Migrates the shared services and FM's own config. No bench version is touched, though a host-wide cutover (like the v1.0.0 rename) may rewrite bench files and briefly take every bench down, because the shared services are every bench's database and only route in. When already current it says so and exits 0.

`--dry-run` prints that same plan (or the "already at vX" line above when there is nothing to do) and exits 0 without migrating or prompting -- including under `--non-interactive`, which the real run refuses without `--yes`. That makes it the scriptable way to ask "is a migration pending?" before deciding whether to run for real: check the printed line rather than the exit code, since a pending and an up-to-date host both exit 0. `fm migrate BENCH --dry-run` / `fm migrate all --dry-run` is the equivalent probe for the bench tier, below.

### Benches {#migrate-bench}

```bash
fm migrate mybench.localhost      # one bench
fm migrate all                    # every bench, one at a time
```

Refused while the global services & configuration are behind: run `fm services migrate` first. Before starting, FM lists what it will do and asks once:

```
Benches:
  • mybench.localhost: v0.19.0 → v1.0.0
  • prod.localhost: v0.19.0 → v1.0.0

Migration versions:
  • v1.0.0

Do you want to proceed?
  yes - Start migration
  no - Abort and revert to previous fm version
```

Answering `no` prints the `uv tool install frappe-manager==<previous>` command to get back to the CLI you came from. `--yes` answers yes for you; `--exclude-bench` (with `all`) leaves named benches alone; `--rerun` re-applies the current release's steps to an already-current target. Every flag: [`fm migrate`](../commands/migrate.md), [`fm services migrate`](../commands/services.md#fm-services-migrate).

!!! tip "Check for a pending migration without running one"
    `fm migrate BENCH --dry-run` (or `fm migrate all --dry-run`) prints the same plan shown above and exits 0 without prompting or migrating anything, non-interactively included. When nothing is pending it prints nothing and still exits 0, so a script should treat empty output as "not migrated" rather than trust the exit code alone. `fm services migrate --dry-run` is the same probe for the services tier, except it always prints one line either way (`already at vX`, or the plan).

!!! info "Running benches are recreated"
    The bench does not need to be stopped first. If it is running, FM warns that its containers will be restarted (recreated) during migration. Stop it with `fm stop mybench` beforehand only if you want to pick the downtime window yourself.

---

## Backups

Every migration backs up what it is about to touch, per bench and per version, before that version's steps run. Backups are never deleted as a side effect: a successful run prints a hint when old sessions exceed the configured keep, and trimming is an explicit command (`fm prune BENCH`, `fm services prune`; retention in the [`[prune]` config table](configuration.md#fm-prune)). The layout on disk, restoring by hand, and the troubleshooting recipes live in [Backup & Restore](../guides/backup-restore.md#before-a-migration); version-specific extra artifacts are listed in the [Migration History](migration-history.md#version-backups).

### Skipping backups {#skip-backups}

Skips are by KIND, because the two kinds cost differently and guard against different damage: the config-file backups are near-free and restore a broken migration's file state, the per-site database dumps are the slow, large half and are the route back when a migration damaged data.

```bash
fm migrate all --skip-db-backup       # keep the cheap config backups, skip the dumps
fm migrate all --skip-config-backup   # the reverse
fm migrate all --skip-backup          # skip both kinds
```

`fm services migrate` takes the same three flags. There, `--skip-db-backup` covers whole-engine dumps like v1.0.0's pre-upgrade dump, where the dump is the only route back from a one-way engine upgrade: reach for it only when taking the dump is genuinely impossible, and prefer it over `--skip-backup`, which also throws away the near-free config backups the rollback restores.

!!! danger "No backups means no rollback"
    Rollback restores files from the backup directory. Without it, a failed migration leaves the bench where it stopped. Use these only when you have external backups, when backup creation itself is what is failing (disk space, permissions), or on disposable benches.

!!! note "Undeterminable database name"
    If FM cannot work out a bench's database name from `site_config.json`, `bench_config.toml`, or the global service info, it asks whether to continue without a database backup. `--skip-backup` or `--skip-db-backup` answers that in advance.

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
fm migrate all --yes --on-failure=archive
```

Each failed bench is rolled back to its last successfully completed migration version and its directory is moved from `~/frappe/sites/<bench>/` to `~/frappe/archived/<bench>/`. The benches that succeeded stay migrated. FM prints which benches it archived.

!!! warning "Not available for a single bench"
    On a single-bench run FM prints `--on-failure=archive not supported for single bench migrations. Using rollback.` and then falls through to the single-bench question above: roll the bench back, or leave it as it is. Archive only makes sense when there are other benches to keep migrated.

### `fm services migrate --on-failure` {#services-on-failure}

The services tier defaults to `rollback` and adds `halt`: leave everything exactly as the failure left it and report where the backups are, for an operator who wants to inspect the wreckage before deciding. `prompt` asks between the two. There is no `archive`: setting a failed bench aside has no meaning for the host-wide tier.

---

## How Migrations Are Selected {#internals}

Migration classes are discovered from the modules in `migration_manager/migrations/` and filtered by `from_version < migration.version <= current_version`, then sorted by version.

`from_version` is the lower of FM's recorded infrastructure version and the lowest version among the targeted benches, so one run can catch a bench that is several releases behind an already-current infrastructure.

For each selected version, in ascending order:

1. If the global services & configuration are behind: back up the services compose file, then apply that version's service changes.
2. For each targeted bench, in sequence: back up, then apply that version's bench changes. A bench already at or above the version is skipped (unless `--rerun`).

Benches are never migrated in parallel. Once a bench has failed, later versions skip it instead of compounding the damage.

**See also:** [Migration History](migration-history.md), [Process Locks](locks.md), [Backup & Restore](../guides/backup-restore.md), [`fm migrate`](../commands/migrate.md)
