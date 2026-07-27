## `fm switch`

Switch a bench to an existing image tag (no bake) -- forward deploys and
rollbacks are the same full pipeline pointed at different tags. --previous
targets the last deployed tag with migrate defaulted OFF; --restore-db also
restores the recorded pre-migrate dump.

**Usage**:

```console
$ fm switch BENCHNAME TAG [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.  [required]
* `TAG`: Full image tag to switch to (e.g. local/mybench:20260721-abc123). Omit with --previous to roll back.

**Options**:

* `--previous`: Target the previously deployed tag (rollback). Implies --no-migrate unless --migrate is passed explicitly.
* `--migrate/--no-migrate`: Override the migrate setting from bench config (\[switch] table) for this run only (config supports true/false/'auto').
* `--restore-db`: Restore the pre-migrate DB dump recorded for the current deploy before the swap (code and data go back together).
* `--rolling/--no-rolling`: Force/disable the rolling web swap. Default: auto (rolling whenever the overlap is safe: no migrate/restore, additive-asserted, or under a maintenance window).


## Examples

### Switch a bench to an already-built image tag

Deploys an existing image tag without baking. Full pipeline: migrate per [switch] config, hooks, backup, rolling web swap when eligible.

```bash
fm switch mybench local/mybench:20260721-abc123
```

### Roll back to the previously deployed image

Full pipeline pointed backwards. --previous defaults migrate OFF (old code must never migrate a newer schema); rolling zero-drop swap when eligible.

```bash
fm switch mybench --previous
```

### Roll back code AND database

Also restores the pre-migrate DB dump recorded during the current deploy -- undoes a bad migrate. Runs under the maintenance window like a migrate.

```bash
fm switch mybench --previous --restore-db
```

