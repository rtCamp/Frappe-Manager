# Installation

This page shows how to install Frappe Manager. Choose the installation method that matches your needs.

## Before you install

- [ ] **Python 3.13**: needed only to run the fm tool itself. fm requires `>=3.13,<3.14`; `uv` downloads a matching interpreter for you. Benches use their own Python inside containers, set per bench with `fm create --python`.
- [ ] **Docker**: Docker Desktop (Mac/Windows) or Docker Engine (Linux). Benches run inside Docker containers, and your user needs permission to use Docker without root.
- [ ] **Git**: fm runs `git ls-remote` on the host to check that every app repo and ref you ask for exists before it starts building. The clones themselves happen inside the container.
- [ ] **Ports 80 and 443 free**: the global nginx proxy binds both on the host and routes every bench by domain.

!!! tip "Quick checks"
    ```bash
    python3 --version
    docker --version
    git --version
    ```

On Windows, see the [WSL guide](../guides/wsl.md).

!!! note "Which tool owns which flag"
    fm does not install itself. That first install belongs to `uv`, `pipx`, or `pip`, so `--python`, `--from`, `--reinstall`, `--force`, and `--upgrade` on this page are **their** flags, not fm's. Once fm is on your PATH, `fm --version` and `fm self update` are fm's own.

## Stable Release (Recommended)

📦 **For production use and general development**

Install the latest stable release from PyPI. This is the recommended option for most users.

=== "uv (Recommended)"

    ```bash
    # Run a single command without installing (great for trying it out)
    uvx --python 3.13 --from frappe-manager fm --help

    # Install permanently
    uv tool install --python 3.13 frappe-manager

    # Upgrade later
    uv tool upgrade frappe-manager
    ```

    `--from` tells `uvx` which package provides the `fm` executable. `--python 3.13` makes uv build the tool environment on 3.13, downloading that interpreter when the system has none.

=== "pipx"

    ```bash
    # Install stable release
    pipx install --python 3.13 frappe-manager

    # Upgrade later
    pipx upgrade frappe-manager
    ```

    Unlike uv, pipx does not download interpreters by default. If 3.13 is not already installed locally, either install it yourself or add `--fetch-python missing`.

=== "pip"

    ```bash
    # Not recommended for system installs; prefer uv or pipx
    pip install frappe-manager
    ```

    This uses whichever interpreter owns that `pip`, so it only gets you current fm if that interpreter is 3.13.

!!! warning "Keep the `--python 3.13`"
    Installing on an older interpreter does not fail. Every installer here resolves the newest fm release whose `requires-python` that interpreter satisfies, so a 3.12 environment silently gets you **fm 0.18.0** instead of an error, and that build then crashes on import against current dependencies. Pin the interpreter and you get the current release or a clear resolution error.

## Development Version

🚧 **For testing unreleased features and contributing**

Install the latest development version directly from the GitHub `develop` branch.

!!! warning "Unstable code"
    The development version contains unreleased features and may be unstable. Only use this if you're:

    - Testing new features before release
    - Contributing to Frappe Manager development
    - Reporting bugs that may already be fixed

=== "uv"

    ```bash
    # Install development version
    uv tool install --python 3.13 git+https://github.com/rtcamp/frappe-manager@develop

    # Try without installing
    uvx --python 3.13 --from git+https://github.com/rtcamp/frappe-manager@develop fm --help

    # Pull the latest commits on develop
    uv tool install --python 3.13 --force --reinstall git+https://github.com/rtcamp/frappe-manager@develop
    ```

    `--force` overwrites the existing install; `--reinstall` refreshes uv's cache, which is what actually picks up new commits on the branch.

=== "pipx"

    ```bash
    # Install development version
    pipx install --python 3.13 git+https://github.com/rtcamp/frappe-manager@develop

    # Pull the latest commits on develop
    pipx install --force --python 3.13 git+https://github.com/rtcamp/frappe-manager@develop
    ```

!!! note "`fm self update` will not move a dev build"
    A dev build is ahead of the released version on PyPI, so `fm self update` reports it as up to date and leaves it alone rather than downgrade the CLI under benches a newer fm wrote. Re-run the install command above instead.

## Verify the install

```bash
fm --version
```

If the command is not found, the tool's bin directory is not on your `PATH`. Fix it with `uv tool update-shell` or `pipx ensurepath`, then open a new shell.

## What gets installed where

Frappe Manager keeps everything under `~/frappe/`. Set `FRAPPE_MANAGER_HOME` to move that workspace somewhere else.

| Directory | What lives there |
|---|---|
| `~/frappe/sites/` | One directory per bench: its config, compose files and workspace |
| `~/frappe/services/` | The shared MariaDB server and nginx proxy every bench uses |
| `~/frappe/logs/` | CLI logs; see [Logs](../reference/logs.md) |
| `~/frappe/backups/` | Pre-migration backups |
| `~/frappe/archived/` | Benches a failed `fm migrate` rolled back and set aside (its `archive` failure action) |
| `~/frappe/fm_config.toml` | Global fm config; see [Configuration](../reference/configuration.md) |

## Upgrading fm

Run these two commands, in this order. The first updates the CLI; the second brings fm's own config, the global services, and your benches up to match it.

```bash
fm self update
fm migrate all
```

Do not skip the second one: every bench command refuses to run against a bench that is behind the installed fm. See [Migrations](../reference/migrations.md) for what `fm migrate` does and how its backups and rollback work.

## Next steps

- [Quick Start](quick-start.md): create your first bench.
- [Concepts](../concepts/index.md): five minutes on the mental model.
