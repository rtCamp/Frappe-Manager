# `fm info`

Show a bench's URL, credentials, apps, deploy history and live service state.

Every secret on the card is printed in cleartext: the administrator password, the site database password, and the basic auth password while a surface is protected. The shared mariadb root credentials moved to fm services info.

**Usage**:

```console
$ fm info BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

**Options**:

* `--json`: Emit this command's result as JSON on clean stdout.  [default: false]

## Examples

### Show everything about a bench

```bash
fm info mybench
```

### Read one fact out of it

fm info mybench --json | jq -r '.url'

```bash
fm info mybench --json
```
