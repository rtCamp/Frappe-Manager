# Guides

Everyday bench workflows. They apply to any bench - dev or prod, on your laptop or a server. (New here? Read [Concepts](../concepts/index.md) first: five minutes, and everything below makes sense. Shipping immutable releases is its own journey: [Deployment](deployment.md).)

## The daily loop

<div class="grid cards" markdown>

-   :lucide-code-2:{ .lg .middle } &nbsp; **[VSCode Integration](vscode.md)**

    ---

    Open a bench in VS Code with a pre-configured debugger. Set breakpoints and inspect live requests.

-   :lucide-puzzle:{ .lg .middle } &nbsp; **[App Management](app-management.md)**

    ---

    Install, update, and pin Frappe apps - at create time or any time after with `fm update --apps`.

-   :lucide-package:{ .lg .middle } &nbsp; **[Python & Node Versions](python-node-versions.md)**

    ---

    Pin toolchain versions per bench, or let fm auto-detect them from Frappe's requirements.

-   :lucide-wrench:{ .lg .middle } &nbsp; **[Admin Tools](admin-tools.md)**

    ---

    Mailpit for email testing and Adminer for database inspection - enabled by default on dev benches.

-   :lucide-cpu:{ .lg .middle } &nbsp; **[fmx: In-Container Services](fmx.md)**

    ---

    Control the supervisor-managed processes inside a bench: restart safely, drain jobs, debug stuck services.

</div>

## Security & data

<div class="grid cards" markdown>

-   :lucide-shield-check:{ .lg .middle } &nbsp; **[SSL / HTTPS](./ssl.md)**

    ---

    Trusted local certificates for development, free auto-renewing Let's Encrypt for public benches.

-   :lucide-archive:{ .lg .middle } &nbsp; **[Backup & Restore](backup-restore.md)**

    ---

    Back up site data from the CLI or Frappe UI, know where backups live, restore when needed.

-   :lucide-database:{ .lg .middle } &nbsp; **[External Database](external-database.md)**

    ---

    Connect a bench to an external MariaDB server instead of the built-in one.

</div>

## Platform & tuning

<div class="grid cards" markdown>

-   :lucide-upload:{ .lg .middle } &nbsp; **[Upload Limits](upload-limits.md)**

    ---

    Raise the maximum file upload size for a bench.

-   :lucide-globe:{ .lg .middle } &nbsp; **[Google API Development](google-api.md)**

    ---

    Configure OAuth credentials so your bench can use Google APIs during local development.

-   :lucide-monitor:{ .lg .middle } &nbsp; **[Windows / WSL](wsl.md)**

    ---

    Run Frappe Manager on Windows using WSL 2. Notes on filesystem performance and browser access.

</div>
