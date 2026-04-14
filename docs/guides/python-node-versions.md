# Python & Node Versions

Every Frappe bench needs a specific Python version for its virtual environment and a specific Node version for asset compilation. Frappe Manager handles both automatically — but you can pin exact versions if you need to.

## How it works

Inside each bench container, FM uses two modern version managers:

- **uv** — manages Python and virtual environments
- **fnm** — manages Node

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

**What happens when you update Python:**

1. FM recreates the virtual environment from scratch using `uv`.
2. All currently installed apps are reinstalled into the new venv.
3. Web and worker services are restarted to pick up the new environment.

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
    Using an incompatible Python or Node version can break your bench. Only use `--skip-version-check` if you know what you are doing — for example, testing a new Frappe branch that has not yet updated its declared requirements.

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

## The uv flag

By default, all new benches use `uv` for Python package management (`use_uv = true` in `bench_config.toml`). uv is significantly faster than pip for installing apps.

There is no reason to disable this unless you have an app that is incompatible with uv's resolver. If that happens, contact the app author — uv compatibility is standard in modern Frappe apps.
