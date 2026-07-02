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

## How the debugger works

When you run `fm code mybench --debugger`, FM writes a `.vscode/tasks.json` into the bench workspace that includes a task called `fm-kill-port`. Before the debugger attaches, VS Code runs this task to stop the Gunicorn web server:

```bash
fmx stop frappe && sleep 2
```

This is necessary because the debugger (`debugpy`) needs to bind to the same port that Gunicorn is using. Once you stop the debug session, Gunicorn is restarted automatically.

The `fmx stop frappe` command talks directly to supervisord inside the container via a Unix socket — it is faster and cleaner than restarting the whole bench. See the [fmx guide](./fmx.md) for more on what fmx can do.
