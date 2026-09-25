# Installation

## Before you install

- [ ] **Python 3.13 or 3.14**: needed only to run the fm tool itself. fm requires `>=3.13,<3.15`; `uv` downloads a matching interpreter for you. Benches use their own Python inside containers, set per bench with `fm create --python`.
- [ ] **Docker**: Docker Desktop (macOS/Windows) or Docker Engine (Linux). Benches run inside Docker containers, and your user needs permission to use Docker without root. fm checks the daemon on every command and exits if it is down.
- [ ] **Git**: fm runs `git ls-remote` on the host to check that every app repo and ref you ask for exists before it starts building. The clones themselves happen inside the container.
- [ ] **Ports 80 and 443 free**: the shared nginx proxy binds both on the host and routes every bench by domain.

!!! tip "Quick checks"
    ```bash
    python3 --version
    docker --version
    git --version
    ```

!!! note "Which tool owns which flag"
    fm does not install itself. That first install belongs to `uv`, `pipx`, or `pip`, so `--python`, `--from`, `--reinstall`, `--force`, and `--upgrade` on this page are **their** flags, not fm's. Once fm is on your `PATH`, `fm --version` and `fm self upgrade` are fm's own.

## Install a stable release

📦 **For production use and general development.** The latest release from PyPI, and what most people want.

=== "uv"

    ```bash
    uv tool install --python 3.13 frappe-manager
    ```

    To try it without installing:

    ```bash
    uvx --python 3.13 --from frappe-manager fm --help
    ```

    `--from` tells `uvx` which package provides the `fm` executable. Upgrade later with `uv tool upgrade frappe-manager`.

=== "pipx"

    ```bash
    pipx install --python 3.13 frappe-manager
    ```

    Unlike uv, pipx does not download interpreters. If 3.13 is not already on the machine, install it yourself or add `--fetch-python missing`. Upgrade later with `pipx upgrade frappe-manager`.

=== "pip"

    ```bash
    pip install frappe-manager
    ```

    Not recommended for a system install. This uses whichever interpreter owns that `pip`, so it only gets you current fm if that interpreter is 3.13 or 3.14.

!!! warning "Keep the `--python` pin"
    Installing on an older interpreter does not fail. Every installer here resolves the newest fm release whose `requires-python` that interpreter satisfies, so a 3.12 environment silently gets you **fm 0.18.0** instead of an error, and that build then crashes on import against current dependencies. Pin the interpreter and you get the current release or a clear resolution error.

## Install the development version

🚧 **For testing unreleased features and contributing.** The `develop` branch carries unreleased, possibly broken code.

!!! warning "Unstable code"
    A dev build can break in ways a release will not. Only use it if you are testing an unreleased feature, contributing to fm, or chasing a bug that may already be fixed.

=== "uv"

    ```bash
    uv tool install --python 3.13 git+https://github.com/rtcamp/frappe-manager@develop
    ```

    To pull later commits on the branch:

    ```bash
    uv tool install --python 3.13 --force --reinstall git+https://github.com/rtcamp/frappe-manager@develop
    ```

    `--force` overwrites the existing install; `--reinstall` refreshes uv's cache, which is what actually picks up new commits.

=== "pipx"

    ```bash
    pipx install --python 3.13 git+https://github.com/rtcamp/frappe-manager@develop
    ```

    To pull later commits, repeat the command with `--force`.

!!! note "`fm self upgrade` will not move a dev build"
    A dev build is ahead of the released version on PyPI, so `fm self upgrade` reports it as up to date and leaves it alone rather than downgrade the CLI under benches a newer fm wrote. Re-run the install command above instead.

## Verify

```bash
fm --version
```

If the command is not found, the tool's bin directory is not on your `PATH`. Run `uv tool update-shell` or `pipx ensurepath`, then open a new shell.

## Windows

fm runs on Windows through WSL 2. Install it inside the WSL distro exactly as you would on Linux, and use Docker Desktop's WSL 2 backend with integration enabled for that distro so fm can reach the Docker socket.

Keep `~/frappe/` on the Linux filesystem (`/home/youruser/frappe`), never under `/mnt/c/`. Bench workspaces are bind-mounted into containers and cross-filesystem mounts are slow.

Windows 11 resolves `*.localhost` on its own. Windows 10 may need an entry in `C:\Windows\System32\drivers\etc\hosts`:

```text
127.0.0.1 mybench.localhost
```

## What gets installed where

Everything lives under `~/frappe/`. Set `FRAPPE_MANAGER_HOME` to move that workspace.

| Directory | What lives there |
|---|---|
| `~/frappe/sites/` | One directory per bench: its config, compose files and workspace |
| `~/frappe/services/` | The shared MariaDB server and nginx proxy every bench uses |
| `~/frappe/logs/` | CLI logs; see [Logs](../reference/logs.md) |
| `~/frappe/backups/` | Pre-migration backups |
| `~/frappe/archived/` | Benches a failed `fm migrate` rolled back and set aside (its `archive` failure action) |
| `~/frappe/fm_config.toml` | Global fm config; see [Configuration](../reference/configuration.md) |

## Upgrading fm

Run these three commands in this order. The first updates the CLI, the second brings fm's own config and the shared services up to match it, the third brings your benches up to match those.

```bash
fm self upgrade
fm services migrate
fm migrate all
```

Do not skip a step. `fm migrate` refuses to run while the shared services and configuration are behind, and every other bench command refuses to run against a bench that is behind the installed fm. [Migrations](../reference/migrations.md) covers what each command does and how its backups and rollback work.
