# Python & Node Versions

Every Frappe bench needs a specific Python version for its virtual environment and a specific Node version for asset compilation. Frappe Manager handles both automatically, but you can pin exact versions if you need to.

## How it works

Inside each bench container, fm uses two version managers:

- **uv**: Python interpreters and the bench virtual environment
- **fnm**: Node

For anything you did not pin yourself, fm reads the requirement off the cloned `frappe` app: `requires-python` in `pyproject.toml` and `engines.node` in `package.json`. Only the `frappe` app is consulted. If the interpreter and Node already in the container satisfy that requirement, fm keeps them; otherwise it installs a matching version. A standard Frappe or ERPNext bench needs nothing from you.

## Setting versions at create time

If you want a specific version, pass `--python` and/or `--node` when creating:

```bash
fm create mybench --python 3.11
fm create mybench --node 20
fm create mybench --python 3.11 --node 20
```

A constraint string is accepted too, but fm does not resolve the range: it pulls one version number out of the string and installs exactly that. For Python it takes the first `major.minor` it finds; for Node, the first run of digits.

```bash
fm create mybench --python ">=3.11,<3.14"   # installs 3.11, not 3.13
fm create mybench --node ">=20"             # installs 20
```

So a range gets you its lower bound. Pass the plain version you want (`--python 3.13`, `--node 22`) unless the floor is genuinely what you meant.

!!! note "`fm create` does not check compatibility"
    The check against Frappe's declared requirements runs in `fm update` only, and there it is a hard error rather than a warning. `fm create` installs whatever you ask for.

## Changing versions on an existing bench

Use `fm update` to change Python or Node on a running bench:

```bash
fm update mybench --python 3.12
fm update mybench --node 20
```

!!! note "Mount benches only"
    Changing Python/Node needs an editable workspace (the default `mount` runtime). On an `image` bench the toolchain is baked into the image; rebuild and ship it with `fm deploy` (see the [Deployment guide](../deploy/index.md)), or demote first with `fm update mybench --runtime mount`.

**What happens when you update Python:**

1. fm reads the venv's interpreter (`env/bin/python`). If it already satisfies the requested requirement, fm prints `already satisfies ... skipping installation` and does nothing further to Python. That skip applies whether or not you passed `--recreate-python-env`.
2. Otherwise fm picks an interpreter already under `.uv/python` that fits, or runs `uv python install`, and repoints the `.uv/python-default` symlink at it.
3. With `--recreate-python-env` (the default), `env/` is renamed to `env.bak-<timestamp>` and a fresh venv is built on that interpreter.
4. Apps are reinstalled only when step 3 actually rebuilt the venv.

`--no-recreate-python-env` stops after step 2: the interpreter is installed and becomes the bench default, but `env/` keeps whatever it was built with, and no apps are reinstalled.

```bash
fm update mybench --python 3.14 --no-recreate-python-env
```

!!! warning "`fm info` reports the default interpreter, not the venv's"
    `fm info` and a bare `python` inside the bench both resolve through `.uv/python-default`, which step 2 has already moved. After `--no-recreate-python-env` they show the new version while the bench still runs the old one out of `env/`. Read `env/bin/python` directly to see what the bench actually uses.

**What happens when you update Node:**

1. fm reads `node --version` in the container. If that major already satisfies the request, fm skips the install.
2. Otherwise `fnm install <major>` runs in the container. Only the major version is ever used: `>=20`, `20.11.0` and `20.x` all mean `20`.
3. `fnm default <major>` makes it the bench default.

Either way, `fm update` restarts the web and worker services at the end.

!!! warning
    A venv rebuild reinstalls every app from scratch and takes a few minutes depending on how many are installed. Do not interrupt it.

## Skipping the compatibility check

`fm update` checks that your requested version satisfies Frappe's declared requirements. If it does not, the command fails with an error and a hint for a compatible version.

To bypass the check (not recommended):

```bash
fm update mybench --python 3.10 --skip-version-check
```

!!! danger
    Using an incompatible Python or Node version can break your bench. Only use `--skip-version-check` if you know what you are doing; for example, testing a new Frappe branch that has not yet updated its declared requirements.

## Checking current versions

```bash
fm info mybench
```

The `runtime` section lists `python` and `node`. On a `mount` bench they are read from the `.uv/python-default` and fnm default symlinks; on an `image` bench, from the `fm.python.version` and `fm.node.version` labels baked onto the image.

From the shell:

```bash
fm shell mybench -c "python --version"
fm shell mybench -c "/workspace/frappe-bench/env/bin/python --version"
fm shell mybench -c "node --version"
```

The first reports the uv default interpreter; the second reports the one the bench's venv was built with. They differ after `--no-recreate-python-env`.

## Package management with uv

Every Python dependency install runs `uv pip install` against the bench venv (`/workspace/frappe-bench/env/bin/python`), and a failed install is retried once with the same command. There is no pip path and no `use_uv` key in `bench_config.toml`.

!!! info "See also"
    [App Management](app-management.md): the same `fm update` command also adds apps or switches an app to another branch/ref.
