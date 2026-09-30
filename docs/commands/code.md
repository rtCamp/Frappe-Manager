# `fm code`

Prepare a bench for VSCode and attach to its running frappe container.

Preparing is the durable half and always happens: the container carries fm's devcontainer metadata (extensions, the user VSCode runs as, editor settings), and VSCode applies it however you connect. Needs the bench up; --force-start starts it.

Attaching launches VSCode on THIS machine, so it needs the 'code' CLI on PATH. Without it fm prepares the bench and tells you how to connect from another one -- Remote-SSH to this host, then 'Dev Containers: Attach to Running Container' -- which is the normal way to use a bench on a server. --no-attach asks for that explicitly.

--debugger writes the debug configuration and installs ruff. An image-mode bench has no mounted workspace, so both live inside that container only and are lost on the next deploy or switch.

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
* `--attach/--no-attach`: Launch VSCode on THIS machine and attach it to the bench's container. --no-attach prepares the bench and stops, which is what a server wants: connect later with Remote-SSH plus 'Dev Containers: Attach to Running Container'. Without the flag fm attaches when the 'code' CLI is available and prepares-and-explains when it is not.  [default: true]

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
