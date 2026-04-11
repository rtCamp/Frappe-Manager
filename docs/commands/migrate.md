## `fm migrate`

Migrate Frappe Manager to current version.

Migration operates at two levels:
- FM Infrastructure: CLI config + global database services (always checked and migrated if needed)
- Benches: Individual bench environments (you choose which ones to migrate)

Without arguments, migrates only FM infrastructure. Specify a benchname to migrate that bench,
or use --all-benches to migrate all benches. Use --auto-proceed to skip confirmation prompts.
Control failure handling with --on-failure: prompt (ask), archive (save failed), or rollback (revert all).

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
* `--on-failure`: What to do if migration fails: prompt (ask user), archive (save failed benches), rollback (revert all)


**Examples**:

_Migrate FM infrastructure only (safe)_
```bash
fm migrate
```

_Migrate specific bench_
```bash
fm migrate
```

_Migrate all benches_
```bash
fm migrate --all-benches
```

_Skip confirmation prompt_
```bash
fm migrate --all-benches --auto-proceed
```

_Auto-proceed with auto-rollback on failure_
```bash
fm migrate --all-benches --auto-proceed --on-failure=rollback
```

_Auto-proceed, archive failed benches (partial success OK)_
```bash
fm migrate --all-benches --auto-proceed --on-failure=archive
```

_Skip all backups (dangerous)_
```bash
fm migrate --all-benches --skip-all-backup
```

_Exclude specific benches_
```bash
fm migrate --all-benches --exclude-bench mybench1,mybench2
```

