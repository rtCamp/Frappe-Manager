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


## Examples

### Open bench in VSCode

Opens the bench workspace in VSCode and attaches the recommended extensions and settings.

```bash
fm code mybench
```

### Open bench with debugger config

Launches VSCode with debugger configuration prepared for the Frappe app.

```bash
fm code mybench --debugger
```

### Force start bench before opening

Starts the bench containers before opening VSCode if they are not running.

```bash
fm code mybench --force-start
```

### Add custom VSCode extension

Installs or enables additional VSCode extensions inside the development container.

```bash
fm code mybench --extension vscodevim.vim
```

### Open with custom working directory

Overrides the default working directory used within the VSCode container.

```bash
fm code mybench --work-dir /workspace
```

