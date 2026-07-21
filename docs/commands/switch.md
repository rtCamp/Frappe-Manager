## `fm switch`

Switch a bench to an existing image tag (no bake). Rolling (blue-green) web
swap when eligible, else recreate-swap.

**Usage**:

```console
$ fm switch BENCHNAME TAG [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.  [required]
* `TAG`: Full image tag to switch to (e.g. local/mybench:20260721-abc123).  [required]

**Options**:

* `--rolling/--no-rolling`: Force/disable the rolling (blue-green) web swap. Default: auto (rolling when the deploy is no-migrate or asserts an additive migration).


## Examples

### Switch a bench to an already-built image tag

Deploys an existing image tag without baking. Runs the full recreate-swap pipeline.

```bash
fm switch mybench local/mybench:20260721-abc123
```

