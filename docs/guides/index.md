# Guides

In-depth walkthroughs for everything Frappe Manager can do.

<div class="grid cards" markdown>

-   :lucide-toggle-left:{ .lg .middle } &nbsp; **[Environments](environments.md)**

    ---

    Switch between development and production modes. Understand what changes between the two and when to use each.

-   :lucide-rocket:{ .lg .middle } &nbsp; **[Deployment — Image Benches](deployment.md)**

    ---

    Bake your bench into an immutable image, deploy it with zero-downtime rolling swaps, roll back with `fm switch --previous`, and prune old releases.

-   :lucide-shield-check:{ .lg .middle } &nbsp; **[SSL / HTTPS](ssl.md)**

    ---

    Enable HTTPS for any bench with a free Let's Encrypt certificate that renews automatically.

-   :lucide-puzzle:{ .lg .middle } &nbsp; **[App Management](app-management.md)**

    ---

    Install, update, and remove Frappe apps from a running bench. Pin apps to specific versions or branches.

-   :lucide-wrench:{ .lg .middle } &nbsp; **[Admin Tools](admin-tools.md)**

    ---

    Access Mailpit for email testing and Adminer for database inspection — enabled by default on dev benches.

-   :lucide-code-2:{ .lg .middle } &nbsp; **[VSCode Integration](vscode.md)**

    ---

    Open a bench in VS Code with a pre-configured debugger. Set breakpoints and inspect live requests.

-   :lucide-monitor:{ .lg .middle } &nbsp; **[Windows / WSL](wsl.md)**

    ---

    Run Frappe Manager on Windows using WSL 2. Notes on filesystem performance and browser access.

-   :lucide-database:{ .lg .middle } &nbsp; **[External Database](external-database.md)**

    ---

    Connect a bench to an external MariaDB server instead of the built-in one.

-   :lucide-upload:{ .lg .middle } &nbsp; **[Upload Limits](upload-limits.md)**

    ---

    Raise the maximum file upload size for a bench.

-   :lucide-globe:{ .lg .middle } &nbsp; **[Google API Development](google-api.md)**

    ---

    Configure OAuth credentials so your bench can use Google APIs during local development.

-   :lucide-cpu:{ .lg .middle } &nbsp; **[fmx — In-Container Services](fmx.md)**

    ---

    Control the supervisor-managed processes inside a bench container — restart workers safely, drain jobs before migrations, and debug stuck services.

-   :lucide-package:{ .lg .middle } &nbsp; **[Python & Node Versions](python-node-versions.md)**

    ---

    Pin specific Python or Node versions for a bench, or let FM auto-detect them from Frappe's requirements. Covers uv, fnm, and version constraint syntax.

-   :lucide-archive:{ .lg .middle } &nbsp; **[Backup & Restore](backup-restore.md)**

    ---

    Back up your site data from the CLI or Frappe UI, understand where backups live, and restore a bench from a previous backup.

</div>
