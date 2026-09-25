# Getting started

Two steps from nothing to a running Frappe bench on your machine.

<div class="grid cards" markdown>

-   :lucide-package:{ .lg .middle } &nbsp; **[Installation](installation.md)**

    ---

    Prerequisites, then install with `uv`, `pipx`, or `pip`. Also: dev builds, Windows under WSL 2, where fm keeps its files, and how to upgrade.

-   :lucide-play:{ .lg .middle } &nbsp; **[Your first bench](quick-start.md)**

    ---

    `fm create mybench`, open the site, change the admin password, and the handful of commands you will use every day.

</div>

## What you need

| Requirement | Detail |
|---|---|
| Docker | Engine 20.10+ with the Compose v2 plugin, daemon running |
| Python | 3.13 or 3.14, to run the `fm` tool itself |
| Git | fm checks every app repo and ref on the host before it builds |
| Ports | 80 and 443 free for the shared nginx proxy |

Details and the commands to check each one: [Before you install](installation.md#before-you-install).

## After your first bench

<div class="grid cards" markdown>

-   :lucide-layers:{ .lg .middle } &nbsp; **[How fm works](../concepts/index.md)**

    ---

    Five minutes on the model: the runtime and environment axes, and what each one decides.

-   :lucide-book-open:{ .lg .middle } &nbsp; **[Guides](../guides/index.md)**

    ---

    Install apps, serve a real domain, issue certificates, back up, attach an external database.

-   :lucide-ship:{ .lg .middle } &nbsp; **[Deployment](../deploy/index.md)**

    ---

    Bake the bench into an immutable image and ship it with a zero-downtime swap.

</div>
