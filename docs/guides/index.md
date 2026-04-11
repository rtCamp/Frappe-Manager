# Guides

In-depth walkthroughs for everything Frappe Manager can do.

<div class="grid cards" markdown>

-   :lucide-toggle-left:{ .lg .middle } &nbsp; **Environments**

    ---

    Switch between development and production modes. Understand what changes between the two and when to use each.

    [:octicons-arrow-right-24: Environments](environments.md)

-   :lucide-shield-check:{ .lg .middle } &nbsp; **SSL / HTTPS**

    ---

    Enable HTTPS for any bench with a free Let's Encrypt certificate that renews automatically.

    [:octicons-arrow-right-24: SSL / HTTPS](ssl.md)

-   :lucide-puzzle:{ .lg .middle } &nbsp; **App Management**

    ---

    Install, update, and remove Frappe apps from a running bench. Pin apps to specific versions or branches.

    [:octicons-arrow-right-24: App Management](app-management.md)

-   :lucide-wrench:{ .lg .middle } &nbsp; **Admin Tools**

    ---

    Access Mailpit for email testing and Adminer for database inspection — available on every bench by default.

    [:octicons-arrow-right-24: Admin Tools](admin-tools.md)

-   :lucide-code-2:{ .lg .middle } &nbsp; **VSCode Integration**

    ---

    Open a bench in VS Code with a pre-configured debugger. Set breakpoints and inspect live requests.

    [:octicons-arrow-right-24: VSCode Integration](vscode.md)

-   :lucide-monitor:{ .lg .middle } &nbsp; **Windows / WSL**

    ---

    Run Frappe Manager on Windows using WSL 2. Notes on filesystem performance and browser access.

    [:octicons-arrow-right-24: Windows / WSL](wsl.md)

-   :lucide-database:{ .lg .middle } &nbsp; **External Database**

    ---

    Connect a bench to an external MariaDB server instead of the built-in one.

    [:octicons-arrow-right-24: External Database](external-database.md)

-   :lucide-upload:{ .lg .middle } &nbsp; **Upload Limits**

    ---

    Raise the maximum file upload size for a bench.

    [:octicons-arrow-right-24: Upload Limits](upload-limits.md)

-   :lucide-globe:{ .lg .middle } &nbsp; **Google API Development**

    ---

    Configure OAuth credentials so your bench can use Google APIs during local development.

    [:octicons-arrow-right-24: Google API Development](google-api.md)

-   :lucide-cpu:{ .lg .middle } &nbsp; **fmx — In-Container Services**

    ---

    Control the supervisor-managed processes inside a bench container — restart workers safely, drain jobs before migrations, and debug stuck services.

    [:octicons-arrow-right-24: fmx](fmx.md)

-   :lucide-package:{ .lg .middle } &nbsp; **Python & Node Versions**

    ---

    Pin specific Python or Node versions for a bench, or let FM auto-detect them from Frappe's requirements. Covers uv, fnm, and version constraint syntax.

    [:octicons-arrow-right-24: Python & Node Versions](python-node-versions.md)

-   :lucide-archive:{ .lg .middle } &nbsp; **Backup & Restore**

    ---

    Back up your site data from the CLI or Frappe UI, understand where backups live, and restore a bench from a previous backup.

    [:octicons-arrow-right-24: Backup & Restore](backup-restore.md)

</div>
