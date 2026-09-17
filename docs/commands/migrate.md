## `fm migrate`

Bring benches up to the current version. Benches only: fm's own global services & configuration are migrated by fm services migrate, never implicitly by this command, and this command refuses to run while they are behind.

Most bench commands refuse to run against a bench that is behind, so migrate first: the exceptions are stop and delete, which must keep working on a bench you cannot migrate, and the image commands bake, switch and prune.

**Usage**:

```console
$ fm migrate BENCH|all [OPTIONS]
```

**Arguments**:

* `BENCH|all`: Bench to act on, or 'all' for every bench fm manages. Omit to pick from the benches you have.

**Options**:

* `--skip-backup`: Skip every pre-migration backup (DANGEROUS; use only when the backups themselves fail).
* `--skip-config-backup`: Skip the config-file backups (bench config, compose files, site configs).
* `--skip-db-backup`: Skip the per-site database dumps (DANGEROUS; the dumps are the rollback path for a failed migration).
* `--exclude-bench`: Bench to leave alone (repeatable; commas also accepted). Only with the 'all' address.
* `--auto-proceed`: Migrate without asking for confirmation.
* `--rerun`: Re-run the migration steps on a bench that is already up to date.
* `--on-failure`: What to do when a bench fails: prompt (ask, the default), archive (set failed benches aside and keep the rest migrated), rollback (revert every bench). A single-bench run always rolls back.


## Examples

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
