## `fm code`

Open a bench in VSCode, attached to its running frappe container.

Needs the bench up (--force-start starts it) and the VSCode 'code' CLI on PATH. An image-mode bench has no mounted workspace, so edits made here live only in that container and are lost on the next deploy or switch.

**Usage**:

```console
$ fm code BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--user`: User VSCode connects as inside the container.
* `-e, --extension`: Extra VSCode extension to install alongside fm's defaults, e.g. ms-python.python (repeatable).
* `-f, --force-start`: Start the bench first if it is not running.
* `-d, --debugger`: Write the Frappe debug launch config and install ruff in the container. Workspace directories only.
* `-w, --work-dir`: Directory VSCode opens inside the container.


## Examples

### Open the bench in VSCode

```bash
fm code mybench
```

### Open it with the Frappe debug config

```bash
fm code mybench --debugger
```

### Add your own extension

```bash
fm code mybench -e vscodevim.vim
```

