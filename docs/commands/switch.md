## `fm switch`

Switch a bench to an already-built image tag, or roll back.

Every switch records the tag you left, so --previous returns to it; run it twice and you are back where you started. Older releases stay until fm prune clears them.

**Usage**:

```console
$ fm switch BENCHNAME TAG [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.  [required]
* `TAG`: Image tag to switch to. Omit when using --previous.

**Options**:

* `--previous`: Roll back to the previously deployed tag, with migrate disabled.
* `--migrate/--no-migrate`: Force or skip bench migrate for this run, overriding the bench config.
* `--restore-db`: Also restore the DB dump taken during the deploy you are undoing.
* `--keep`: After a successful deploy, prune old releases keeping the newest N (minimum 1; see fm prune).
* `--rolling/--no-rolling`: Force or disable the rolling web swap; the default is automatic whenever the overlap is safe. Forcing it is only safe when both versions run against the same database schema.


## Examples

### Switch to a tag you baked

fm bake prints the tag; fm info lists the ones this bench has already run.

```bash
fm switch mybench local/mybench:20260721-abc123
```

### Switch to a tag from a registry

Pulled with your ambient docker login when it is not already local.

```bash
fm switch mybench ghcr.io/acme/mybench:v15.2.1
```

### Roll back the last deploy

```bash
fm switch mybench --previous
```

### Roll back code and database together

For when the migration is the problem: the dump taken before it goes back with the older code.

```bash
fm switch mybench --previous --restore-db
```

### Roll back more than one release

--previous only knows the last tag, so name an older one explicitly and keep migrate off.

```bash
fm switch mybench local/mybench:20260718-9f21e0 --no-migrate
```

## Related

- [Deployment guide](../deploy/index.md)
- [Rolling back](../deploy/rollback.md)
