## `fm code`

Open bench in vscode.

**Usage**:

```console
$ fm code BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--user`: User to connect as
* `-e, --extension`: VSCode extensions to install (e.g., ms-python.python)
* `-f, --force-start`: Start bench before opening VSCode
* `-d, --debugger`: Setup debugger config
* `-w, --work-dir`: Working directory in VSCode


**Examples**:

_Open bench in VSCode_
```bash
fm code mybench
```

_Open bench with debugger config_
```bash
fm code mybench --debugger
```

_Force start bench before opening_
```bash
fm code mybench --force-start
```

_Add custom VSCode extension_
```bash
fm code mybench --extension vscodevim.vim
```

_Open with custom working directory_
```bash
fm code mybench --work-dir /workspace
```

