## `fm migrate`

Migrate Frappe Manager to current version.

Migration operates at two levels:
- FM Infrastructure: CLI config + global database services (always checked and migrated if needed)
- Benches: Individual bench environments (you choose which ones to migrate)

Without arguments, migrates only FM infrastructure. Specify a benchname to migrate that bench,
or use --all-benches to migrate all benches. Use --auto-proceed to skip confirmation prompts.
Control failure handling with --on-failure: prompt (ask), archive (save failed), or rollback (revert all).
Use --rerun to test idempotency — re-applies all migration steps even when already up to date.

**Usage**:

```console
$ fm migrate BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Bench name to migrate

**Options**:

* `--all-benches`: Migrate all benches
* `--skip-all-backup`: Skip all backups (DANGEROUS - use only if backups fail)
* `--skip-backup-for`: Skip backup for specific benches (comma-separated)
* `--exclude-bench`: Exclude specific benches from migration (only with --all-benches)
* `--auto-proceed`: Skip migration confirmation prompt (proceed automatically)
* `--rerun`: Re-run migration even if already migrated (for testing idempotency). Config transforms and supervisor regeneration are re-applied, but the runtime environment is only rebuilt when Python/Node versions change.
* `--on-failure`: What to do if migration fails: prompt (ask user), archive (save failed benches), rollback (revert all)


## Examples

### Migrate FM infrastructure only (safe)

Applies only FM infrastructure migrations without touching benches. Safe to run for CLI updates.

```bash
fm migrate
```

### Migrate specific bench

Runs migration steps for a specific bench to bring it in line with the FM version.

```bash
fm migrate mybench
```

### Migrate all benches

Applies migrations to all benches managed by FM. Use with caution and consider backups.

```bash
fm migrate --all-benches
```

### Skip confirmation prompt

Runs migrations across all benches without interactive prompts. Useful for automation.

```bash
fm migrate --all-benches --auto-proceed
```

### Auto-proceed with auto-rollback on failure

Automatically proceeds with migrations and rolls back if a failure occurs.

```bash
fm migrate --all-benches --auto-proceed --on-failure=rollback
```

### Auto-proceed, archive failed benches (partial success OK)

Archives benches that fail migration while continuing others; useful for large fleets.

```bash
fm migrate --all-benches --auto-proceed --on-failure=archive
```

### Skip all backups (dangerous)

Disables taking backups before migration. This is risky and should only be used in controlled scenarios.

```bash
fm migrate --all-benches --skip-all-backup
```

### Exclude specific benches

Excludes specific benches from a full migration run.

```bash
fm migrate --all-benches --exclude-bench mybench1,mybench2
```

