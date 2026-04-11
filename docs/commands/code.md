# fm code

Open a bench in Visual Studio Code. This attaches the editor to the container so you can edit files and run commands in the integrated terminal.

Usage:

```console
$ fm code BENCHNAME [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `-e, --extension` | Add VSCode extension by identifier |
| `-f, --force-start` | Force the bench to start before attaching |
| `-d, --debugger` | Start with debugger support enabled |

Example:

```bash
fm code mybench -e ms-python.python --debugger
```
