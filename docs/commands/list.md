## `fm list`

List all benches.

Shows a table with status, runtime (mount/image), environment, installed apps,
the deployed tag / base image, and path. --json emits the full inventory
(including alias domains, seed provenance, restart policy) for scripting.

**Usage**:

```console
$ fm list [OPTIONS]
```

**Options**:

* `--json`: Output the bench inventory as JSON (clean stdout, pipe-friendly).
* `-p, --paths`: Plain 'bench  path' lines (no table): copy- and pipe-friendly, never truncated.


## Examples

### List all available benches

Shows a table of all benches managed by FM with status, runtime, apps and deploy info.

```bash
fm list
```

### Machine-readable output

Emits the full bench inventory as JSON (status, runtime, environment, apps, tags, domains, policies) for scripting: fm list --json | jq '.[].name'.

```bash
fm list --json
```

