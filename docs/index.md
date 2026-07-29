---
hide:
  - navigation
  - toc
---

# Frappe Manager

<div class="grid cards" markdown>

-   :lucide-rocket:{ .lg .middle } &nbsp; **[Up and running in minutes](getting-started/quick-start.md)**

    ---

    Create a fully configured Frappe bench (with Docker, database, and web server) using a single command. No manual setup required.

-   :lucide-layers:{ .lg .middle } &nbsp; **[One model, two axes](concepts/index.md)**

    ---

    Every bench is described by its runtime (editable workspace or immutable image) and environment (dev or prod). Run as many as you need, side by side.

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

    Inspect emails in Mailpit, browse the database in Adminer, and monitor background jobs, all built in, no configuration needed.

-   :lucide-ship:{ .lg .middle } &nbsp; **[Ship immutable deploys](deploy/index.md)**

    ---

    Bake your bench into a Docker image and deploy it with zero-downtime rolling swaps. Roll back code (and database) in one command with `fm switch --previous`.

</div>

## Install

```bash
uv tool install --python 3.13 frappe-manager
```

Other methods (pipx, uvx, dev builds): see the [Installation guide](getting-started/installation.md).

## Create your first bench

```bash
fm create mybench
```

Your bench is ready at **http://mybench.localhost**; log in with `Administrator` / `admin`.

!!! tip "Need ERPNext?"
    ```bash
    fm create mybench --apps erpnext
    ```

## Where to go next

<div class="grid" markdown>

!!! info "New to Frappe Manager?"

    Start with the [Installation guide](getting-started/installation.md) (prerequisites in [Before you install](getting-started/installation.md#before-you-install)), then follow the [Quick Start](getting-started/quick-start.md).

!!! example "Already installed?"

    Learn the model in [Concepts](concepts/index.md), work the daily loop in the [Guides](guides/index.md), and ship with the [Deployment guide](deploy/index.md).

</div>
