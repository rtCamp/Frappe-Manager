## `fm migrate`

Bring Frappe Manager and its benches up to the current version.

Benches are never migrated implicitly: a bare fm migrate updates only FM's own config and global services. Name a bench, or say 'all', to migrate benches themselves.

Most bench commands refuse to run against a bench that is behind, so migrate first: the exceptions are stop and delete, which must keep working on a bench you cannot migrate, and the image commands bake, switch and prune.

**Usage**:

```console
$ fm migrate BENCH|all [OPTIONS]
```

**Arguments**:

* `BENCH|all`: Bench to act on, or 'all' for every bench fm manages. Omit to act on nothing but fm itself.

**Options**:

* `--skip-all-backup`: Migrate without taking a pre-migration backup (DANGEROUS; use only when the backups themselves fail).
* `--skip-backup-for`: Skip the pre-migration backup for these benches only (comma-separated).
* `--exclude-bench`: Benches to leave alone (comma-separated). Only with the 'all' address.
* `--auto-proceed`: Migrate without asking for confirmation.
* `--rerun`: Re-run the migration steps on a bench that is already up to date.
* `--on-failure`: What to do when a bench fails: prompt (ask, the default), archive (set failed benches aside and keep the rest migrated), rollback (revert every bench). A single-bench run always rolls back.


## Examples

### Migrate FM itself after a CLI update

Updates FM's own config and global services. No bench is touched.

```bash
fm migrate
```

### Migrate one bench

```bash
fm migrate mybench
```

### Migrate every bench

'all' goes where a bench name goes: it is an address meaning every bench fm manages, not a flag.

```bash
fm migrate all
```

### Migrate every bench unattended

The combination for CI and large fleets: no prompts, and one bad bench does not undo the others.

```bash
fm migrate all --auto-proceed --on-failure=archive
```

### Leave some benches behind

```bash
fm migrate all --exclude-bench mybench1,mybench2
```

## Related

- [Migrations](../reference/migrations.md)
