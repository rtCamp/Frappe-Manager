## `fm code`

Open bench in VSCode.

Attaches VSCode to the bench container with recommended extensions and optional debugger support.

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
Opens the bench workspace in VSCode and attaches the recommended extensions and settings.
```bash
fm code mybench
```

_Open bench with debugger config_
Launches VSCode with debugger configuration prepared for the Frappe app.
```bash
fm code mybench --debugger
```

_Force start bench before opening_
Starts the bench containers before opening VSCode if they are not running.
```bash
fm code mybench --force-start
```

_Add custom VSCode extension_
Installs or enables additional VSCode extensions inside the development container.
```bash
fm code mybench --extension vscodevim.vim
```

_Open with custom working directory_
Overrides the default working directory used within the VSCode container.
```bash
fm code mybench --work-dir /workspace
```

