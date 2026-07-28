# Python & Node Versions

Every Frappe bench needs a specific Python version for its virtual environment and a specific Node version for asset compilation. Frappe Manager handles both automatically - but you can pin exact versions if you need to.

## How it works

Inside each bench container, FM uses two modern version managers:

- **uv** - manages Python and virtual environments
- **fnm** - manages Node

When you create a bench, FM reads the `frappe` app's `pyproject.toml` (`requires-python`) and `package.json` (`engines.node`) to find out which versions Frappe needs, then installs them automatically. You do not need to do anything for a standard Frappe or ERPNext bench.

## Setting versions at create time

If you want a specific version, pass `--python` and/or `--node` when creating:

```bash
fm create mybench --python 3.11
fm create mybench --node 20
fm create mybench --python 3.11 --node 20
```

You can also use version constraint syntax (same format as `pyproject.toml`):

```bash
fm create mybench --python ">=3.11,<3.14"
fm create mybench --node ">=20"
```

FM validates your choice against Frappe's requirements and warns you if it looks incompatible.

## Changing versions on an existing bench

Use `fm update` to change Python or Node on a running bench:

```bash
fm update mybench --python 3.12
fm update mybench --node 20
```

!!! note "Mount benches only"
    Changing Python/Node needs an editable workspace (the default `mount` runtime). On an `image` bench the toolchain is baked into the image - rebuild and ship it with `fm deploy` (see the [Deployment guide](deployment.md)), or demote first with `fm update mybench --runtime mount`.

**What happens when you update Python:**

1. FM installs the requested Python via `uv` and recreates the virtual environment from scratch.
2. All currently installed apps are reinstalled into the new venv.
3. Web and worker services are restarted to pick up the new environment.

To install a new Python as the default **without** touching the existing venv, pass `--no-recreate-python-env`:

```bash
fm update mybench --python 3.14 --no-recreate-python-env
```

**What happens when you update Node:**

1. FM installs the requested Node version via `fnm` inside the container.
2. The new version is set as the default.
3. Web and worker services are restarted.

!!! warning
    Changing Python recreates the entire virtual environment. This takes a few minutes depending on how many apps are installed. Do not interrupt the process.

## Skipping the compatibility check

FM checks that your requested version satisfies Frappe's declared requirements. If it does not, FM prints an error and a hint for a compatible version.

To bypass the check (not recommended):

```bash
fm update mybench --python 3.10 --skip-version-check
```

!!! danger
    Using an incompatible Python or Node version can break your bench. Only use `--skip-version-check` if you know what you are doing - for example, testing a new Frappe branch that has not yet updated its declared requirements.

## Checking current versions

```bash
fm info mybench
```

`fm info` shows the Python and Node versions that are currently active inside the bench.

You can also run a quick check from the shell:

```bash
fm shell mybench -c "python --version"
fm shell mybench -c "node --version"
```

## Package management with uv

All benches use `uv` for Python package management (with pip as an automatic fallback for packages uv cannot handle). This is built in and not configurable - there is no `use_uv` setting in `bench_config.toml`.

!!! info "See also"
    [App Management](app-management.md) - the same `fm update` command also adds apps or switches an app to another branch/ref.
