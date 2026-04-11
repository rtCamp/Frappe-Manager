## `fm info`

Show bench information and configuration.

Displays bench status, installed apps, environments, and other relevant configuration.

**Usage**:

```console
$ fm info BENCHNAME
```

**Arguments**:

* `BENCHNAME`: Name of the bench.


**Examples**:

_Show bench details and configuration_
Displays bench status, environment type, apps installed, and other configuration details useful for debugging and documentation.
```bash
fm info mybench
```

_View info in verbose mode_
Shows additional diagnostic information including container states and compose file paths.
```bash
fm info mybench --verbose
```

