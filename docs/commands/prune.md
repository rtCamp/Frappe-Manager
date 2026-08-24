## `fm prune`

Delete old deploy releases: history rows, their DB dumps, and their local image tags.

Keeps the newest keep_releases from the bench config (7 by default) or --keep. Nothing else is touched: a dump or image survives while a kept release, the current or previous tag, or the seed or base image still needs it, so rollback stays possible.

**Usage**:

```console
$ fm prune BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.  [required]

**Options**:

* `--keep`: Keep this many releases instead of the configured keep_releases. Minimum 1: the current release is never pruned.
* `--dry-run`: Report what would be pruned without deleting anything.


## Examples

### See what a prune would remove

```bash
fm prune mybench --dry-run
```

### Prune old releases now

fm switch can do the same inline with --keep N.

```bash
fm prune mybench
```

### Keep only the last 3 releases

```bash
fm prune mybench --keep 3
```

## Related

- [Deployment guide](../deploy/index.md)
