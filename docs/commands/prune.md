## `fm prune`

Remove old deploy releases (history, DB dumps, unused image tags).

Keeps the newest N releases per keep_releases in bench config (--keep overrides). Current and previous tags -- and any dump a kept release still references -- are never touched.

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

Keeps the newest releases per keep_releases in bench config (default 7); current and previous tags are always safe. Also available inline: --keep N on fm deploy/switch.

```bash
fm prune mybench
```

### Keep only the last 3 releases

One-off override of the configured retention.

```bash
fm prune mybench --keep 3
```

