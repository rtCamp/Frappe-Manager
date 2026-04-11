---
hide:
  - navigation
  - toc
---

# Frappe Manager

<div class="grid cards" markdown>

-   :lucide-rocket:{ .lg .middle } &nbsp; **Up and running in minutes**

    ---

    Create a fully configured Frappe bench — with Docker, database, and web server — using a single command. No manual setup required.

    [:octicons-arrow-right-24: Quick Start](getting-started/quick-start.md)

-   :lucide-layers:{ .lg .middle } &nbsp; **Manage multiple benches**

    ---

    Run as many independent benches as you need on one machine. Switch between development and production, or run them side by side.

    [:octicons-arrow-right-24: Environments](guides/environments.md)

-   :lucide-shield-check:{ .lg .middle } &nbsp; **SSL made automatic**

    ---

    Add HTTPS to any bench with Let's Encrypt. Certificates are provisioned and renewed without any manual work.

    [:octicons-arrow-right-24: SSL / HTTPS](guides/ssl.md)

-   :lucide-code-2:{ .lg .middle } &nbsp; **Built for developers**

    ---

    Open any bench directly in VS Code with a debugger ready to go. Switch to development mode and see live changes instantly.

    [:octicons-arrow-right-24: VSCode Integration](guides/vscode.md)

-   :lucide-puzzle:{ .lg .middle } &nbsp; **Install any Frappe app**

    ---

    Add ERPNext, HRMS, or any custom app at creation time or any time afterwards. Apps are pinned to specific branches or versions.

    [:octicons-arrow-right-24: App Management](guides/app-management.md)

-   :lucide-wrench:{ .lg .middle } &nbsp; **Admin tools included**

    ---

    Inspect emails in Mailpit, browse the database in Adminer, and monitor background jobs — all built in, no configuration needed.

    [:octicons-arrow-right-24: Admin Tools](guides/admin-tools.md)

</div>

## Install

=== "uv (recommended)"

    ```bash
    uv tool install --python 3.13 frappe-manager
    ```

=== "pipx"

    ```bash
    pipx install frappe-manager
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
