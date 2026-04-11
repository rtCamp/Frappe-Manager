## `fm info`

Show bench information and configuration.

Displays bench status, installed apps, environments, and other relevant configuration.

**Usage**:

```console
$ fm info BENCHNAME
```

**Arguments**:

* `BENCHNAME`: Name of the bench.


## Examples

### Show bench details and configuration

Displays bench status, environment type, apps installed, and other configuration details useful for debugging and documentation.

```bash
fm info mybench
```

### View info in verbose mode

Shows additional diagnostic information including container states and compose file paths.

```bash
fm info mybench --verbose
```

