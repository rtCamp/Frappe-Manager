# VS Code Integration

`fm code` attaches VS Code to a bench's running frappe container, so you edit the mounted workspace with the bench's own Python interpreter and tooling.

Prerequisites: VS Code with the **Dev Containers** extension, and the `code` CLI on your `PATH`. The bench must be running; `fm code` refuses to attach otherwise.

```bash
fm code mybench
```

Start the bench first if it is down:

```bash
fm code mybench --force-start
```

Enable debugger support:

```bash
fm code mybench --debugger
```

## Extensions

fm always installs its own set: debugpy, command-variable, Python, Ruff, ESLint and Prettier. `-e/--extension` **adds** to that set, it does not replace it, and it is repeatable:

```bash
fm code mybench -e ms-toolsai.jupyter -e vscodevim.vim
```

The list and the connecting user are stored as a `devcontainer.metadata` label on the frappe service. Changing either rewrites the label and brings the service back up so VS Code picks it up; running `fm code` again with the same values touches nothing.

## Other options

| Option | What it does |
| --- | --- |
| `--user` | User VS Code connects as inside the container. Defaults to `frappe`. |
| `--work-dir` | Directory VS Code opens. Defaults to `/workspace/frappe-bench`. |

!!! warning "Image-mode benches"
    An `image` bench has no live-mounted workspace, so edits made through `fm code` live only in that container and are lost on the next `fm switch`. `fm code` warns when you do it. Use it to reproduce and observe; ship real changes through the [deployment pipeline](../deploy/index.md).

## How the debugger works

`--debugger` needs a workspace `--work-dir`; point it elsewhere and fm warns and skips the setup. When it does run, it writes `launch.json`, `tasks.json` and `settings.json` into `<work-dir>/.vscode` (backing up any existing file to a timestamped copy first) and installs `ruff` into the bench virtualenv.

`launch.json` carries three configurations:

- **fm-frappe-debug**: `bench serve` on port 80 under debugpy, with `DEV_SERVER=1` and `justMyCode` off, so you can step into framework code.
- **Debug Specific Queue**: a `frappe worker` for a queue you are prompted for.
- **Debug specific fuction**: `frappe execute` on a path you are prompted for.

The first one is the interesting case. It binds port 80, the same port the supervised web server already holds, so it declares a `preLaunchTask` called `fm-kill-port` that VS Code runs first:

```bash
fmx stop frappe && sleep 2
```

When you end the debug session the supervised web server stays stopped. Bring it back with:

```bash
fm shell mybench -c "fmx start frappe"
```

`fmx` talks to supervisord inside the container over a Unix socket, so this cycles one process rather than the whole bench. See the [fmx guide](fmx.md) for the rest of what it can do.
