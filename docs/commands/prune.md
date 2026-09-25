# `fm prune`

Reclaim this bench's disk: old deploy releases, old backup sessions, oversized logs.

Plan-first: the full plan (every path that would be touched) is printed, then one confirmation covers it; a bare Enter aborts, --yes skips the question, --dry-run stops after the plan. Runs all three categories by default; narrow with --only. Retention comes from the \[prune] table (bench overriding host) and \[switch].keep_releases; flags override for one run. Log rotation copies to <name>.log.<timestamp>.gz and truncates the live file in place, because the writing processes hold it open. Nothing in fm cleans up on its own: this command (and fm services prune for the host tier) is the only trigger.

**Usage**:

```console
$ fm prune BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on.  [required]

**Options**:

* `--only [releases|backups|logs]`: Run only this category (repeatable): releases, backups, logs. Default: all three.
* `--keep, --keep-releases INTEGER`: Releases to keep instead of \[switch].keep_releases. Minimum 1: the current release is never pruned.
* `--keep-backups INTEGER`: Backup sessions to keep per location instead of \[prune].keep_backup_sessions.
* `--keep-logs INTEGER`: Rotated archives to keep per log file instead of \[prune].keep_log_archives.
* `--rotate-over TEXT`: Rotate log files larger than this (e.g. '500K', '10M') instead of \[prune].rotate_logs_over.
* `-y, --yes`: Prune without asking for confirmation.  [default: false]
* `--dry-run`: Print the plan and exit without deleting anything; never prompts.  [default: false]

## Examples

### See what a prune would remove, without removing it

```bash
fm prune mybench --dry-run
```

### Everything: old releases, old backup sessions, oversized logs

Shows the full plan (every path) first, then asks. A bare Enter aborts; type y to proceed, or pass --yes.

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

## See also

- [Deployment](../deploy/index.md)
