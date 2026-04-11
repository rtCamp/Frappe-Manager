# VS Code Integration

Prerequisites: Visual Studio Code with the "Dev Containers" extension installed.

Open a bench in VS Code:

```bash
fm code mybench
```

Force start the bench and then attach:

```bash
fm code mybench --force-start
```

Enable debugger support (writes .vscode launch files):

```bash
fm code mybench --debugger
```

Install extra extensions when opening:

```bash
fm code mybench -e ms-toolsai.jupyter
```

Default extensions installed include Python support, Ruff, ESLint, Prettier, and debugpy.

!!! tip
    The bench must be running for a smooth attach. Use `--force-start` to start it automatically if needed.
