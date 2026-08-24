# Guides

Everyday bench workflows, for any bench: dev or prod, on your laptop or a server. New here? Read [Concepts](../concepts/index.md) first, then [Environments: Dev vs Prod](environments.md) for what those two words actually change. For immutable releases and rolling swaps instead, see [Deployment](../deploy/index.md).

## The daily loop

<div class="grid cards" markdown>

-   :lucide-code-2:{ .lg .middle } &nbsp; **[VSCode Integration](vscode.md)**

    ---

    Attach VS Code to a bench's running container: fm's extension set, and a Frappe debug config that steps into framework code.

-   :lucide-puzzle:{ .lg .middle } &nbsp; **[App Management](app-management.md)**

    ---

    Install, update and pin Frappe apps at create time or later with `fm update --apps`, private repos and monorepo subdirectories included.

-   :lucide-package:{ .lg .middle } &nbsp; **[Python & Node Versions](python-node-versions.md)**

    ---

    Pin toolchain versions per bench, or let fm auto-detect them from Frappe's requirements.

-   :lucide-wrench:{ .lg .middle } &nbsp; **[Admin Tools](admin-tools.md)**

    ---

    Mailpit for mail and Adminer for the database, plus the `fm auth` basic auth prompt that can front either the tools or the whole site.

-   :lucide-cpu:{ .lg .middle } &nbsp; **[fmx: In-Container Services](fmx.md)**

    ---

    Control the supervisor-managed processes inside a bench: restart safely, drain jobs, debug stuck services.

</div>

## Domains, security & data

<div class="grid cards" markdown>

-   :lucide-globe-2:{ .lg .middle } &nbsp; **[Domains & Remote Access](domains.md)**

    ---

    How routing works, serving a bench on multiple domains, and tunneling a local bench to the internet.

-   :lucide-server:{ .lg .middle } &nbsp; **[Hosting on a Server](hosting.md)**

    ---

    The end-to-end runbook: fresh server to HTTPS-served production benches, one domain per client.

-   :lucide-shield-check:{ .lg .middle } &nbsp; **[SSL / HTTPS](ssl.md)**

    ---

    Let's Encrypt over HTTP-01 or Cloudflare DNS-01, and fm's own CA for locally trusted development certificates.

-   :lucide-archive:{ .lg .middle } &nbsp; **[Backup & Restore](backup-restore.md)**

    ---

    Three overlapping tools (fm, `bench`, the Frappe UI), which one owns what, where backups land, and how to restore.

-   :lucide-database:{ .lg .middle } &nbsp; **[External Database](external-database.md)**

    ---

    Point a site at your own MariaDB server instead of fm's `global-db`. Declared per site, not per bench.

</div>

## Platform & tuning

<div class="grid cards" markdown>

-   :lucide-upload:{ .lg .middle } &nbsp; **[Upload Limits](upload-limits.md)**

    ---

    Raise the maximum file upload size for a bench.

-   :lucide-monitor:{ .lg .middle } &nbsp; **[Windows / WSL](wsl.md)**

    ---

    Run fm on Windows under WSL 2: where to keep the bench directory, and how `.localhost` resolves.

</div>
