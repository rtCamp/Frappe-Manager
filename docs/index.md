---
hide:
  - navigation
  - toc
---

# Frappe Manager

<div class="grid cards" markdown>

-   :lucide-rocket:{ .lg .middle } &nbsp; **[Up and running in minutes](getting-started/quick-start.md)**

    ---

    Create a fully configured Frappe bench — with Docker, database, and web server — using a single command. No manual setup required.

-   :lucide-layers:{ .lg .middle } &nbsp; **[Manage multiple benches](guides/environments.md)**

    ---

    Run as many independent benches as you need on one machine. Switch between development and production, or run them side by side.

-   :lucide-shield-check:{ .lg .middle } &nbsp; **[SSL made automatic](guides/ssl.md)**

    ---

    Add HTTPS to any bench with Let's Encrypt. Certificates are provisioned and renewed without any manual work.

-   :lucide-code-2:{ .lg .middle } &nbsp; **[Built for developers](guides/vscode.md)**

    ---

    Open any bench directly in VS Code with a debugger ready to go. Switch to development mode and see live changes instantly.

-   :lucide-puzzle:{ .lg .middle } &nbsp; **[Install any Frappe app](guides/app-management.md)**

    ---

    Add ERPNext, HRMS, or any custom app at creation time or any time afterwards. Apps are pinned to specific branches or versions.

-   :lucide-wrench:{ .lg .middle } &nbsp; **[Admin tools included](guides/admin-tools.md)**

    ---

    Inspect emails in Mailpit, browse the database in Adminer, and monitor background jobs — all built in, no configuration needed.

</div>

## Install

<div class="admonition tip" markdown>
<p class="admonition-title">📌 Version-Specific Installation</p>
<div markdown>

- View [stable docs](latest/) for production PyPI install
- View [dev docs](dev/) for development Git install

</div>
</div>

=== "Stable (Recommended)"

    📦 **Production ready** • Install from PyPI

    ```bash
    uv tool install --python 3.13 frappe-manager
    ```

    Using pipx:

    ```bash
    pipx install frappe-manager
    ```

=== "Development"

    🚧 **For testing** • Install from GitHub

    !!! warning
        Development builds may be unstable. Use for testing only.

    ```bash
    uv tool install git+https://github.com/rtcamp/frappe-manager@develop
    ```

    Using pipx:

    ```bash
    pipx install git+https://github.com/rtcamp/frappe-manager@develop
    ```

=== "Try without installing"

    ```bash
    uvx --from frappe-manager fm --help
    ```

## Create your first bench

```bash
fm create mybench
```

Your bench is ready at **http://mybench.localhost** — log in with `Administrator` / `admin`.

!!! tip "Need ERPNext?"
    ```bash
    fm create mybench --apps erpnext
    ```

## Where to go next

<div class="grid" markdown>

!!! info "New to Frappe Manager?"

    Start with [Requirements](getting-started/requirements.md), then follow the [Installation guide](getting-started/installation.md) and [Quick Start](getting-started/quick-start.md).

!!! example "Already installed?"

    Head to the [Guides](guides/index.md) to learn about SSL, app management, VSCode integration, and more.

</div>
