## `fm prune`

Reclaim this bench's disk: old deploy releases, old backup sessions, oversized logs.

Runs all three categories by default; narrow with --only. Retention comes from the \[prune] table (bench overriding host) and \[switch].keep_releases; flags override for one run. Log rotation copies to <name>.log.<timestamp>.gz and truncates the live file in place, because the writing processes hold it open. Nothing in fm cleans up on its own: this command (and fm services prune for the host tier) is the only trigger.

**Usage**:

```console
$ fm prune BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on.  [required]

**Options**:

* `--only`: Run only this category (repeatable): releases, backups, logs. Default: all three.
* `--keep, --keep-releases`: Releases to keep instead of \[switch].keep_releases. Minimum 1: the current release is never pruned.
* `--keep-backups`: Backup sessions to keep per location instead of \[prune].keep_backup_sessions.
* `--keep-logs`: Rotated archives to keep per log file instead of \[prune].keep_log_archives.
* `--rotate-over`: Rotate log files larger than this (e.g. '500K', '10M') instead of \[prune].rotate_logs_over.
* `--dry-run`: Report what would be pruned without deleting anything.


## Examples

### See what a prune would remove, without removing it

```bash
fm prune mybench --dry-run
```

### Everything: old releases, old backup sessions, oversized logs

```bash
fm prune mybench
```

### Only rotate the logs

```bash
fm prune mybench --only logs
```

### Keep only the last 3 releases

```bash
fm prune mybench --only releases --keep-releases 3
```

## Related

- [Deployment guide](../deploy/index.md)
