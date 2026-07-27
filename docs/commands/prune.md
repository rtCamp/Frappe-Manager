## `fm prune`

Prune old deploy releases: history entries, their recorded DB-dump dirs, and local image tags no kept release references. Current and previous tags are always safe.

**Usage**:

```console
$ fm prune BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.  [required]

**Options**:

* `--keep`: Retain this many releases (overrides bench config).
* `--dry-run`: Report what would be pruned without deleting anything.


## Examples

### Preview what a prune would remove

Lists the history entries, backup dirs, and local image tags that would go. Nothing is touched.

```bash
fm prune mybench --dry-run
```

### Prune old releases now

Keeps the newest releases per [switch].releases_retain_limit (default 7) plus whatever is current/previous. Also runs automatically after every successful deploy.

```bash
fm prune mybench
```

### Keep only the last 3 releases

One-off override of the configured retention.

```bash
fm prune mybench --keep 3
```

