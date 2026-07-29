# Migrations

When you update FM CLI itself, run `fm migrate` to upgrade your benches and infrastructure to match the new version.

## Overview

FM migrations operate at two levels:

1. **FM Infrastructure**: Global services (MariaDB, Redis, nginx-proxy) and CLI configuration
2. **Benches**: Individual bench environments, configurations, and compose files

Migrations are **version-aware**: FM tracks which version each component is migrated to and only applies necessary upgrade steps.

!!! tip "Quick Start"
    **After updating FM:**
    ```bash
    # Update CLI first
    fm self update
    
    # Migrate infrastructure only (safe)
    fm migrate
    
    # Migrate specific bench
    fm migrate mybench
    
    # Migrate all benches
    fm migrate --all-benches
    ```

!!! warning "Always update CLI before migrating"
    Run `fm self update` first, then `fm migrate`. Running migrations on an old CLI version can cause compatibility issues.

---

## How Migration Works

### Migration Process

1. **Pre-flight checks**: Validates current state and determines required migrations
2. **Backup creation**: Creates backups of config files + MariaDB dump
3. **Apply migrations**: Executes version-specific upgrade steps
4. **Verification**: Validates successful migration
5. **State update**: Records the new version in `[migration_state]`

### What Gets Backed Up

| Component | Backup Location | Restoreable |
|---|---|---|
| Bench config files | `~/frappe/sites/<bench>/backups/migrations/<timestamp>/` | ✅ Yes |
| MariaDB database | Same location (gzipped SQL dump, per migration version) | ✅ Yes |
| Docker compose files | Same as bench config | ✅ Yes |
| Global config (`fm_config.toml`) | `~/frappe/backups/migrations/<timestamp>/` | ✅ Yes |

!!! info "Backup timestamp format"
    Backups use format: `DD-Mon-YY--HH-MM-SS` (e.g., `12-Apr-26--14-30-45`)

### Version Tracking

FM tracks migration state in two places:

**Global infrastructure:** `~/frappe/fm_config.toml`
```toml
[migration_state]
system_migrated_to = "0.19.0"
```

**Per-bench:** `~/frappe/sites/<benchname>/bench_config.toml`
```toml
[migration_state]
migrated_to = "0.19.0"
last_migration_date = "2026-04-12T14:30:45"
```

---

## Migration Commands

### Migrate Infrastructure Only {#migrate-infrastructure}

```bash
fm migrate
```

Applies only FM infrastructure migrations (global services, CLI config). **Safe to run after every CLI update.**

**When to use:**

- After running `fm self update`
- No benches need upgrading yet
- Just want to update global services

---

### Migrate Specific Bench {#migrate-bench}

```bash
fm migrate mybench
```

Migrates a single bench to current FM version.

**What happens:**

1. Backs up `bench_config.toml`, `docker-compose*.yml`, MariaDB database
2. Applies bench-specific migration steps (SSL config format, compose changes, etc.)
3. Updates `migration_state.migrated_to` in bench config

!!! info "Running benches are restarted"
    The bench does **not** need to be stopped first. If it is running, FM warns that its containers will be restarted (recreated) during migration. Stop it beforehand with `fm stop mybench` only if you want to control the downtime window yourself.

---

### Migrate All Benches {#migrate-all-benches}

```bash
fm migrate --all-benches
```

Migrates all benches in `~/frappe/sites/`.

**Confirmation prompt:**

```
Benches needing migration:
  • mybench: v0.18.0 → v0.19.0
  • testbench: v0.18.0 → v0.19.0
  • prod: v0.18.0 → v0.19.0

Do you want to proceed? (yes / no)
```

**Skip prompt:**

```bash
fm migrate --all-benches --auto-proceed
```

---

### Re-run a Migration {#rerun}

```bash
fm migrate mybench --rerun
```

Re-applies all migration steps even when the target is already up to date (for testing idempotency). Config transforms and supervisor regeneration are re-applied; the runtime environment is only rebuilt when Python/Node versions change.

---

### Exclude Benches {#exclude-benches}

```bash
fm migrate --all-benches --exclude-bench oldbench,legacy
```

Migrate all benches **except** specified ones (comma-separated).

!!! tip "Use for problematic benches"
    Exclude broken or archived benches from mass migrations, then handle them individually later.

---

## Failure Handling

### On-Failure Strategies {#on-failure}

Control what happens when a bench migration fails:

#### `--on-failure=prompt` (default) {#on-failure-prompt}

Asks what to do after a failure.

```bash
fm migrate --all-benches --on-failure=prompt
```

**Single bench:** you are asked whether to roll the bench back to its pre-migration state, or skip rollback and leave it as-is for manual fixing / retry.

**Multiple benches (`--all-benches`):** you are asked whether to **archive** the failed benches (continue with the rest) or **revert the migration for all benches**.

---

#### `--on-failure=rollback` {#on-failure-rollback}

Automatically roll back on failure.

```bash
fm migrate mybench --on-failure=rollback
```

**What happens:**

1. Migration fails
2. Backups are restored (config files, docker-compose files, MariaDB dump)
3. For a single bench: that bench returns to its pre-migration state. For `--all-benches`: the migration is reverted for **all** benches
4. Exit with error

!!! tip "Recommended for production benches"
    Use rollback for critical production benches to ensure they never end up in a half-migrated state.

---

#### `--on-failure=archive` {#on-failure-archive}

Move failed benches to the archive, continue migrating others (partial success OK).

```bash
fm migrate --all-benches --on-failure=archive
```

**What happens:**

1. Migration fails for `mybench`
2. `mybench` is rolled back to its last successfully completed migration version
3. Moves `~/frappe/sites/mybench/` → `~/frappe/archived/mybench/`
4. Continues migrating remaining benches, printing a summary of archived benches at the end

!!! warning "Only for `--all-benches`"
    Archive mode requires `--all-benches`. For a single bench migration it falls back to rollback.

**Use case:** Large bench fleets where some failures are acceptable, and you'll investigate archived benches later.

---

## Backup Management

### Skip All Backups (Dangerous) {#skip-backups}

```bash
fm migrate --all-benches --skip-all-backup
```

!!! danger "Use only in controlled scenarios"
    Skipping backups means **no rollback possible** if migration fails. Only use when:
    
    - You have external backups
    - Backup creation itself is failing (disk space, permissions)
    - Testing in disposable dev environments

---

### Skip Backups for Specific Benches {#skip-backup-for}

```bash
fm migrate --all-benches --skip-backup-for testbench,devbench
```

Skip backups for specified benches (comma-separated), still back up others.

**Use case:** Dev benches with large databases where backup time is prohibitive.

---

## Backup Restoration

### Manual Rollback

If you need to manually restore a bench from backup:

```bash
# Find backup timestamp
ls ~/frappe/sites/mybench/backups/migrations/

# Example: 12-Apr-26--14-30-45
BACKUP_TS="12-Apr-26--14-30-45"

# Stop bench
fm stop mybench

# Restore config files
cp ~/frappe/sites/mybench/backups/migrations/$BACKUP_TS/bench_config.toml \
   ~/frappe/sites/mybench/

# Restore database: copy the dump into the workspace (only the workspace is
# mounted into the containers), then restore inside the bench
cp ~/frappe/sites/mybench/backups/migrations/$BACKUP_TS/*/db-*.sql.gz \
   ~/frappe/sites/mybench/workspace/frappe-bench/sites/
fm start mybench
fm shell mybench -c "bench --site mybench.localhost restore sites/db-mybench-*.sql.gz"
```

!!! info "Database backups are gzipped SQL dumps"
    Use `bench restore` command inside the bench to restore database from `.sql.gz` file.

---

## Version-Specific Migration Notes

### v0.19.0 Notable Changes {#v0-19-0}

**Toolchain changes:**

- Switched from `pyenv` + `nvm` to `uv` + `fnm` for Python/Node version management
- Python/Node versions now auto-detected from app `pyproject.toml`

**SSL configuration changes:**

- Multi-certificate support (individual certs per domain)
- New `ssl_certificates` array format in `bench_config.toml`
- Old single-certificate configs automatically converted

**Migration impact:**

- Requires bench container rebuild (Python/Node toolchain replacement)
- SSL config files regenerated in new format
- Approximately 2-5 minutes per bench (depending on app count)

---

## Troubleshooting

### Migration Fails with Backup Error

**Symptom:** `Error creating backup: Disk quota exceeded`

**Solution:**

```bash
# Check disk space
df -h ~/frappe

# Clean old backups
rm -rf ~/frappe/backups/migrations/<old-timestamp>/

# Or skip backups (if safe)
fm migrate mybench --skip-all-backup
```

---

### Bench Stuck in Half-Migrated State

**Symptom:** Migration failed, bench won't start, rollback wasn't triggered

**Solution:**

```bash
# Find most recent backup
ls ~/frappe/sites/mybench/backups/migrations/ | sort -r | head -1

# Manually restore (see "Manual Rollback" above)
```

---

### Migration Says "Already Up to Date" But Bench Broken

**Symptom:** `fm migrate mybench` says already migrated, but bench config is wrong

**Solution:**

Reset migration state to force re-migration:

```bash
# Edit bench_config.toml
nano ~/frappe/sites/mybench/bench_config.toml

# Remove or change [migration_state] section:
[migration_state]
migrated_to = "0.18.0"  # Set to previous version

# Save and re-run migration
fm migrate mybench
```

---

## Migration Internals

### Migration Discovery

FM automatically discovers and applies migrations by comparing:

1. Current FM CLI version (from `fm --version`)
2. Infrastructure migration state (`[migration_state] system_migrated_to` in `fm_config.toml`)
3. Each bench migration state (`[migration_state] migrated_to` in `bench_config.toml`)

**Migration selection:**

- If CLI = 0.19.0, infrastructure = 0.18.0 → Apply 0.19.0 infrastructure migration
- If CLI = 0.19.0, bench = 0.18.0 → Apply 0.19.0 bench migration
- If CLI = 0.19.0, bench = 0.19.0 → Skip (already migrated)

### Migration Execution Order

1. **Infrastructure migrations** (always first)
   - Global database schema upgrades
   - Global service config changes
   - CLI config format updates

2. **Bench migrations** (one bench at a time)
   - Bench config format changes
   - Docker compose file updates
   - SSL certificate format conversions
   - Per-bench database migrations (if needed)

!!! info "Sequential execution"
    Benches are migrated sequentially (not parallel) to avoid database lock conflicts and ensure proper error handling.

### Migration Check on Every Command

Every `fm` invocation first checks whether the infrastructure or the target bench is behind the CLI version and prompts you to migrate before proceeding. A small whitelist of commands skips this gate so they stay usable on un-migrated setups: `list`, `migrate`, `bake`, `deploy`, `switch`, `self compose`, and `self update-images`. The bench-level check is additionally skipped for `stop` and `delete`.

**See also:** [Configuration reference](/reference/configuration/), [Architecture reference](/reference/architecture/)
