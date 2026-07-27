## `fm switch`

Switch a bench to an already-built image tag (no bake). Roll back with --previous: same pipeline pointed at the last deployed tag, with migrate disabled so old code never runs against a newer schema.

Pipeline: fetch -> pre-flight -> backup -> migrate (per config) -> swap (rolling when safe) -> record.

**Usage**:

```console
$ fm switch BENCHNAME TAG [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.  [required]
* `TAG`: Image tag to switch to. Omit when using --previous.

**Options**:

* `--previous`: Roll back to the previously deployed tag (disables migrate).
* `--migrate/--no-migrate`: Force or skip bench migrate for this run (overrides bench config).
* `--restore-db`: Also restore the DB dump recorded during the current deploy.
* `--rolling/--no-rolling`: Force/disable the rolling web swap (default: auto when the overlap is safe).


## Examples

### Deploy a tag you baked earlier

The everyday forward deploy: `fm bake` printed this tag (also in `fm list`). Runs backup, migrate per config, swap, and records the tag in deploy history.

```bash
fm switch mybench local/mybench:20260721-abc123
```

### Deploy a tag from a registry

Pulls with your ambient docker login if the image is not local. Typical on a prod box where CI pushed the image.

```bash
fm switch mybench ghcr.io/acme/mybench:v15.2.1
```

### Roll back a bad deploy

The 3am command. Returns to the last deployed tag with migrate disabled automatically (old code must never migrate a newer schema). Run it again to undo the rollback.

```bash
fm switch mybench --previous
```

### Roll back further than one release

--previous only knows the last tag; for anything older pass the tag explicitly (recorded in bench_config.toml deploy history) and keep migrate off.

```bash
fm switch mybench local/mybench:20260718-9f21e0 --no-migrate
```

### Undo a bad migration (code AND database)

Also restores the DB dump recorded during the current deploy, so code and schema go back together. Runs under the maintenance window like a migrate.

```bash
fm switch mybench --previous --restore-db
```

### Ship a code-only hotfix without the migrate ceremony

Skips migrate, and with it the maintenance window -- which makes the zero-downtime rolling swap eligible. Fastest safe path for template/py-only fixes.

```bash
fm switch mybench local/mybench:20260721-hotfix1 --no-migrate
```

### Force the zero-downtime rolling swap

Old and new web replicas serve side by side, then the old drains away. Only force it when both versions work against the same DB schema; --no-rolling forces the plain recreate instead.

```bash
fm switch mybench local/mybench:20260722-def456 --rolling
```

