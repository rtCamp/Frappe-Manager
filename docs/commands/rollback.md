## `fm rollback`

Roll back the bench to the previously deployed image tag (no migrate).

**Usage**:

```console
$ fm rollback BENCHNAME
```

**Arguments**:

* `BENCHNAME`: Name of the bench.  [required]


## Examples

### Roll back to the previously deployed image

Re-pins the compose to the previous tag and recreates (no migrate).

```bash
fm rollback mybench
```

