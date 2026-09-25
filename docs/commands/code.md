# `fm code`

Open a bench in VSCode, attached to its running frappe container.

Needs the bench up (--force-start starts it) and the VSCode 'code' CLI on PATH. An image-mode bench has no mounted workspace, so edits made here live only in that container and are lost on the next deploy or switch.

**Usage**:

```console
$ fm code BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

**Options**:

* `--user TEXT`: User VSCode connects as inside the container.  [default: frappe]
* `-e, --extension TEXT`: Extra VSCode extension to install alongside fm's defaults, e.g. ms-python.python (repeatable).  [default: ['ms-python.debugpy', 'rioj7.command-variable', 'ms-python.python', 'charliermarsh.ruff', 'dbaeumer.vscode-eslint', 'esbenp.prettier-vscode']]
* `-f, --force-start`: Start the bench first if it is not running.  [default: false]
* `-d, --debugger`: Write the Frappe debug launch config and install ruff in the container. Workspace directories only.  [default: false]
* `-w, --work-dir TEXT`: Directory VSCode opens inside the container.  [default: /workspace/frappe-bench]

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
