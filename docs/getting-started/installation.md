# Installation

This page shows three ways to install Frappe Manager. `uv` is recommended — it handles Python versions cleanly and keeps things isolated.

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
    # Not recommended for system installs — prefer uv or pipx
    pip install frappe-manager
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
| `~/frappe/migration/` | Migration backups |

!!! note "Next step"
    Head to [Quick Start](quick-start.md) to create your first bench.
