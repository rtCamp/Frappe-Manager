---
hide:
  - navigation
  - toc
---

# Frappe Manager

<div class="grid cards" markdown>

-   :lucide-rocket:{ .lg .middle } &nbsp; **[Up and running in minutes](getting-started/quick-start.md)**

    ---

    One command creates a Frappe bench and starts it: containers, database, workers, and web server. You bring Docker; fm does the rest.

-   :lucide-layers:{ .lg .middle } &nbsp; **[One model, two axes](concepts/index.md)**

    ---

    Every bench is described by its runtime (editable workspace or immutable image) and environment (dev or prod). Run as many as you need, side by side.

-   :lucide-shield-check:{ .lg .middle } &nbsp; **[HTTPS in one command](guides/ssl.md)**

    ---

    Issue a Let's Encrypt certificate for any bench domain with `fm ssl add`, over HTTP-01 or DNS-01. Renewal is `fm ssl renew --all`, which is safe to run from a daily cron.

-   :lucide-code-2:{ .lg .middle } &nbsp; **[Built for developers](guides/vscode.md)**

    ---

    `fm code mybench` opens the bench in VS Code attached to its running container; add `--debugger` for the Frappe debug launch config. On a `mount` bench your edits are live.

-   :lucide-puzzle:{ .lg .middle } &nbsp; **[Install any Frappe app](guides/app-management.md)**

    ---

    Add ERPNext, HRMS, or any custom app with `--apps`, at create time or later on a running `mount` bench. Pin each one to a branch, tag, or commit.

-   :lucide-wrench:{ .lg .middle } &nbsp; **[Admin tools included](guides/admin-tools.md)**

    ---

    Read outgoing mail in Mailpit and browse the database in Adminer, path-routed under the bench URL behind basic auth. On by default for `dev` benches.

-   :lucide-ship:{ .lg .middle } &nbsp; **[Ship immutable deploys](deploy/index.md)**

    ---

    Bake your bench into a Docker image and deploy it with a zero-downtime rolling swap. Roll back with `fm switch --previous`, or `--previous --restore-db` to take the database back with the code.

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

A bare name becomes a `.localhost` domain, so this bench answers on **http://mybench.localhost**. fm starts it and prints the URL and credentials when it finishes; the default login is `Administrator` / `admin`.

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
