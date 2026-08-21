## `fm list`

List all benches with status, runtime, installed apps and deploy state.

A bench whose config will not load is reported as a warning and left out of the listing; every other bench still lists. --json includes it instead, as a row carrying the error.

**Usage**:

```console
$ fm list [OPTIONS]
```

**Options**:

* `--json`: Emit the full inventory as JSON on clean stdout.
* `-p, --paths`: Print plain 'name  path' lines instead of cards, so paths survive copying and piping.


## Examples

### List every bench

```bash
fm list
```

### Copy or pipe bench paths

```bash
fm list --paths
```

### Script over the inventory

fm list --json | jq -r '.[] | select(.status == "active") | .name'

```bash
fm list --json
```

