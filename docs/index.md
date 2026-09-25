---
hide:
  - navigation
---

# Frappe Manager

Frappe Manager (`fm`) runs Frappe and ERPNext benches on Docker. One command builds a bench, starts its containers, and prints the URL and login. The same tool takes that bench to a server, gives it a real domain and a Let's Encrypt certificate, and ships it as an immutable image.

It replaces the hand-rolled stack most Frappe developers end up maintaining: a bench directory, MariaDB, Redis, a supervisor config, an nginx vhost, and a pile of shell aliases.

<div class="grid cards" markdown>

-   :lucide-rocket:{ .lg .middle } &nbsp; **[Up and running in minutes](getting-started/quick-start.md)**

    ---

    One command creates a Frappe bench and starts it: containers, database, workers, and web server. You bring Docker; fm does the rest.

-   :lucide-layers:{ .lg .middle } &nbsp; **[One model, two axes](concepts/index.md)**

    ---

    Every bench is described by its runtime (editable workspace or immutable image) and environment (dev or prod). Run as many as you need, side by side.

-   :lucide-shield-check:{ .lg .middle } &nbsp; **[HTTPS in one command](guides/ssl.md)**

    ---

    Issue a Let's Encrypt certificate for any bench domain with `fm ssl add`, over HTTP-01 or DNS-01, or import one you already have with `--custom`. Renewal is `fm ssl renew all`, safe to run from a daily cron.

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

    Bake your bench into a Docker image and deploy it with a zero-downtime rolling swap. Roll back with `fm switch --previous`, or add `--restore-db` to take the database back with the code.

</div>

## Requirements

| Requirement | Detail |
|---|---|
| Docker | Engine 20.10+ with the Compose v2 plugin, daemon running |
| Python | 3.13 or 3.14 |
| Platform | Linux, macOS, or Windows via WSL 2 |
| Disk | About 4 GB for the first bench, less for each one after |

fm talks to the Docker daemon on every command. If Docker is not running, `fm` exits before it does anything.

## Install

```bash
uv tool install --python 3.13 frappe-manager
```

Then check it:

```bash
fm --version
```

Other installers and how to upgrade: [Installation](getting-started/installation.md).

## Create a bench

```bash
fm create mybench
```

A bare name becomes a `.localhost` domain, so this bench answers on `http://mybench.localhost`. fm creates the bench, starts it, and prints the URL and credentials when it finishes. The default login is `Administrator` / `admin`.

To include ERPNext, name the apps you want:

```bash
fm create mybench --apps erpnext
```

Walk through the rest of the first session in [Your first bench](getting-started/quick-start.md).

## Where to go next

<div class="grid" markdown>

!!! info "New to Frappe Manager?"

    Start with [Installation](getting-started/installation.md), then follow [Your first bench](getting-started/quick-start.md).

!!! example "Already installed?"

    Learn the model in [How fm works](concepts/index.md), work the daily loop in the [Guides](guides/index.md), and ship with the [Deployment guide](deploy/index.md).

</div>
