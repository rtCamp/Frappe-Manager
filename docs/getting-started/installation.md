# Installation

This page shows how to install Frappe Manager. Choose the installation method that matches your needs.

## Stable Release (Recommended)

📦 **For production use and general development**

Install the latest stable release from PyPI. This is the recommended option for most users.

=== "uv (Recommended)"

    ```bash
    # Run a single command without installing (great for trying it out)
    uvx --from frappe-manager fm --help

    # Install permanently
    uv tool install --python 3.13 frappe-manager

    # Upgrade later
    uv tool upgrade frappe-manager
    ```

=== "pipx"

    ```bash
    # Install stable release
    pipx install frappe-manager

    # Upgrade later
    pipx upgrade frappe-manager
    ```

=== "pip"

    ```bash
    # Not recommended for system installs - prefer uv or pipx
    pip install frappe-manager
    ```

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
    uv tool install git+https://github.com/rtcamp/frappe-manager@develop

    # Try without installing
    uvx --from git+https://github.com/rtcamp/frappe-manager@develop fm --help

    # Upgrade to latest develop
    uv tool install --reinstall git+https://github.com/rtcamp/frappe-manager@develop
    ```

=== "pipx"

    ```bash
    # Install development version
    pipx install git+https://github.com/rtcamp/frappe-manager@develop

    # Upgrade to latest develop
    pipx install --force git+https://github.com/rtcamp/frappe-manager@develop
    ```

## Verify the install

```bash
fm --version
```

If the command is not found, make sure the tool's bin directory is on your `PATH`. For `uv`, run:

```bash
uv tool update-shell
```

## What gets installed where

Frappe Manager uses `~/frappe/` as its workspace. After you create your first bench, you'll find:

| Directory | What lives there |
|---|---|
| `~/frappe/sites/` | Your bench folders |
| `~/frappe/services/` | Shared database and proxy |
| `~/frappe/logs/` | CLI logs |
| `~/frappe/backups/` | Migration backups |
| `~/frappe/archived/` | Archived/failed benches moved by fm migrate |

!!! note "Next step"
    Head to [Quick Start](quick-start.md) to create your first bench.
