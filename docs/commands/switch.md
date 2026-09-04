## `fm switch`

Switch a bench to an already-built image, or roll back.

A switch is not just an image change. By default it takes a database backup, raises a maintenance page for the schema-changing steps, and runs bench migrate against the new image, so plan for the site to be briefly unavailable. Each of those is a \[switch] config key and can be turned off there.

Every switch records the image you left, so --previous returns to it; run it twice and you are back where you started. Rolling back does NOT migrate, because old code must never migrate a newer schema; pass --migrate to insist. Older releases stay until fm prune clears them.

**Usage**:

```console
$ fm switch BENCH IMAGE [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on.  [required]
* `IMAGE`: Image to switch to: a full reference such as ghcr.io/acme/mybench:v15.2.1. Omit when using --previous.

**Options**:

* `--previous`: Roll back to the previously deployed image, with migrate disabled.
* `--migrate/--no-migrate`: Force or skip bench migrate for this run, overriding the bench config.
* `--restore-db`: Also restore the DB dump taken during the deploy you are undoing. This REPLACES the current database: the dump drops and recreates every table, so everything written since that deploy is lost. fm asks you to confirm before importing.
* `-y, --yes`: Accept the --restore-db overwrite without being asked. The only way to restore a dump unattended, and the only thing this flag skips.
* `--keep`: After a successful deploy, prune old releases keeping the newest N (minimum 1; see fm prune).
* `--rolling/--no-rolling`: Force or disable the rolling web swap; the default is automatic whenever the overlap is safe. Forcing it is only safe when both versions run against the same database schema.


## Examples

### Switch to an image you baked

fm bake prints the image; fm info lists the ones this bench has already run.

```bash
fm switch mybench local/mybench:20260721-abc123
```

### Switch to an image from a registry

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

### Roll back code and database unattended

Without --yes fm asks you to type the schema name, and refuses when there is no terminal to ask on.

```bash
fm switch mybench --previous --restore-db --yes
```

### Roll back more than one release

--previous only knows the last image, so name an older one explicitly and keep migrate off.

```bash
fm switch mybench local/mybench:20260718-9f21e0 --no-migrate
```

## Related

- [Deployment guide](../deploy/index.md)
- [Rolling back](../deploy/rollback.md)
