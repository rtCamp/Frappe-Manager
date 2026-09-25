# Guides

Task-shaped instructions for a bench that already exists, dev or prod, laptop or server. If you have not read [How fm works](../concepts/index.md) yet, start there: most of these pages assume you know what a runtime and an environment are.

For immutable releases and rolling swaps, see [Deployment](../deploy/index.md) instead.

## The daily loop

<div class="grid cards" markdown>

-   :lucide-puzzle:{ .lg .middle } &nbsp; **[App management](app-management.md)**

    ---

    Install, update and pin Frappe apps at create time or later with `fm apps add`, private repos and monorepo subdirectories included.

-   :lucide-package:{ .lg .middle } &nbsp; **[Python and Node versions](python-node-versions.md)**

    ---

    Pin toolchain versions per bench, or let fm auto-detect them from Frappe's requirements.

-   :lucide-code-2:{ .lg .middle } &nbsp; **[VS Code integration](vscode.md)**

    ---

    Attach VS Code to a bench's running container: fm's extension set, and a Frappe debug config that steps into framework code.

-   :lucide-wrench:{ .lg .middle } &nbsp; **[Admin tools](admin-tools.md)**

    ---

    Mailpit for mail and Adminer for the database, plus the `fm auth` basic auth prompt that can front either the tools or the whole site.

-   :lucide-cpu:{ .lg .middle } &nbsp; **[fmx: in-container services](fmx.md)**

    ---

    Control the supervisor-managed processes inside a bench: restart safely, drain jobs, debug stuck services.

</div>

## Domains, security and data

<div class="grid cards" markdown>

-   :lucide-globe-2:{ .lg .middle } &nbsp; **[Domains and remote access](domains.md)**

    ---

    How routing works, serving a bench on multiple domains, and tunneling a local bench to the internet.

-   :lucide-shield-check:{ .lg .middle } &nbsp; **[HTTPS certificates](ssl.md)**

    ---

    Let's Encrypt over HTTP-01 or Cloudflare DNS-01, fm's own CA for locally trusted development certificates, or a certificate you bring yourself.

-   :lucide-server:{ .lg .middle } &nbsp; **[Hosting on a server](hosting.md)**

    ---

    The end-to-end runbook: fresh server to HTTPS-served production benches, one domain per client.

-   :lucide-archive:{ .lg .middle } &nbsp; **[Backup and restore](backup-restore.md)**

    ---

    Three overlapping tools (fm, `bench`, the Frappe UI), which one owns what, where backups land, and how to restore.

-   :lucide-database:{ .lg .middle } &nbsp; **[External database](external-database.md)**

    ---

    Point a site at your own MariaDB server instead of fm's `mariadb`. Declared per site, not per bench.

</div>

## Elsewhere

A few tasks are documented where the thing they change lives:

| I want to | Page |
|---|---|
| Switch a bench between `dev` and `prod` | [Environments](../concepts/environments.md) |
| Raise the file upload limit | [`upload_limit` reference](../reference/configuration.md#upload-limit) |
| Run fm on Windows | [Installation](../getting-started/installation.md#windows) |
