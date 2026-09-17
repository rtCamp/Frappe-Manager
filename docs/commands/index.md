# Command Reference

Complete reference for all `fm` CLI commands. Each command page includes usage, options, and real-world examples.


## Flag Conventions

Four rules hold across every fm command, so a flag means the same thing everywhere:

- **`--yes` / `-y`** answers any confirmation prompt: "do the thing I typed, don't ask." It never expands what a command does. Every prompt defaults to **No**: a bare Enter aborts. Under `--non-interactive`, an unanswered prompt refuses and names `--yes`.
- **Dangerous behaviors are their own named flags** (`--restore-db`, `--delete-backups`, `--skip-db-backup`, `--on-failure`). A decision that changes *what* happens is never buried in a prompt only: the flag names it, and `--yes` never answers it for you.
- **`--force` selects a stronger action** (recreate containers, interrupt jobs, renew early). It never skips a question.
- **`--dry-run` prints the plan and changes nothing**: exit 0, never prompts. Available on the plan-first commands (`prune`, `services prune`, `migrate`, `services migrate`, `delete`); it is the scriptable way to see a plan, since non-interactive runs without `--yes` refuse instead.

---

## Quick Start

The most common commands to get you started:

<div class="grid cards" markdown>

-   :material-plus-circle:{ .lg .middle } **[Create a bench](create.md)**

    ---

    ```bash
    fm create mybench
    fm create mybench --apps erpnext
    ```

-   :material-play-circle:{ .lg .middle } **[Start & Stop](start.md)**

    ---

    ```bash
    fm start mybench
    fm stop mybench
    ```
    
-   :material-console:{ .lg .middle } **[Run commands](shell.md)**

    ---

    ```bash
    fm shell mybench
    fm shell mybench -c "bench --version"
    ```

-   :material-certificate:{ .lg .middle } **[Add SSL](ssl.md)**

    ---

    ```bash
    fm ssl add mybench/example.com
    fm ssl add mybench/example.com --challenge dns01
    ```

</div>

---

## Bench Lifecycle

Core commands for creating and managing benches.

### :material-plus-circle: [`fm create`](create.md) {.command-heading}
**Create a new bench with apps**

Set up a fresh Frappe development or production environment with your choice of apps, Python/Node versions, and configuration. `--base-image` names the image the bench's containers run: the base frappe image sitting under a mount workspace, or, with `--runtime image`, the pre-built app image itself. `--seed-image` is a different job, mount-only: it fills the workspace once from a baked image, and the bench still boots on the base image afterwards, so the two compose. There is no `--image` on create; that flag belongs to `fm bake` and names the image a bake produces.

```bash
fm create mybench
fm create mybench --apps erpnext --apps hrms
fm create mybench -e prod
fm create prodbench --runtime image --base-image ghcr.io/acme/mysite:v42
fm create mybench --seed-image ghcr.io/acme/mysite:v42
```

### :material-play-circle: [`fm start`](start.md) {.command-heading}
**Start a bench**

Brings up all containers and services for a stopped bench, with optional reconfiguration of supervisor and workers.

```bash
fm start mybench
fm start mybench --force
```

### :material-stop-circle: [`fm stop`](stop.md) {.command-heading}
**Stop a bench**

Shuts down all containers without removing any data.

```bash
fm stop mybench
```

### :material-restart: [`fm restart`](restart.md) {.command-heading}
**Restart bench services**

Restart web and workers via supervisor (default), the whole containers with `--container`, or do a zero-downtime web swap with `--rolling` on image benches.

```bash
fm restart mybench
fm restart mybench --container
fm restart mybench --rolling
```

### :material-delete: [`fm delete`](delete.md) {.command-heading}
**Delete a bench**

Permanently removes a bench directory and optionally its database from the shared mariadb container.

```bash
fm delete mybench
fm delete mybench --delete-db-from-fm-mariadb
```

### :material-format-list-bulleted: [`fm list`](list.md) {.command-heading}
**Show all benches**

List all benches with their status and basic info.

```bash
fm list
fm list --json
```

---

## Development & Debugging

Tools for working with running benches.

### :material-console: [`fm shell`](shell.md) {.command-heading}
**Open shell or run commands**

Execute commands inside containers, open interactive shells, or use the Frappe bench console.

```bash
fm shell mybench
fm shell mybench -c "bench --version"
fm shell mybench --bench-console
```

### :material-microsoft-visual-studio-code: [`fm code`](code.md) {.command-heading}
**Open in VSCode**

Launch VSCode with the bench directory and attach to containers for debugging.

```bash
fm code mybench
fm code mybench --debugger
```

### :material-text-box: [`fm logs`](logs.md) {.command-heading}
**View bench logs**

Stream or display logs from bench services (frappe, nginx, redis, etc.).

```bash
fm logs mybench
fm logs mybench -f
fm logs mybench --service nginx
```

### :material-docker: [`fm compose`](compose.md) {.command-heading}
**Run docker compose on a bench**

Raw docker compose against a bench with all of its compose files already wired up; everything after the bench name is passed through untouched.

```bash
fm compose mybench ps
fm compose mybench logs -f frappe
```

### :material-information: [`fm info`](info.md) {.command-heading}
**Show bench details**

Display comprehensive bench configuration, status, installed apps, and environment info.

```bash
fm info mybench
```

---

## Configuration

Modify bench settings and infrastructure.

### :material-cog: [`fm update`](update.md) {.command-heading}
**Update bench configuration**

Change environment type, Python/Node versions, restart policy, and convert between mount and image runtimes. Apps, alias domains, admin tools and APM monitoring now have their own commands: `fm apps`, `fm domain`, `fm tools` and `fm telemetry` below.

```bash
fm update mybench -e prod
fm update mybench --python 3.11 --node 20
fm update mybench --runtime mount
```

### :material-puzzle: [`fm apps`](apps.md) {.command-heading}
**Manage installed apps**

Fetch app code onto a bench and record it, or install it into one site (`BENCH/SITE`) or every site (`BENCH/all`); installing runs `bench migrate` and restarts.

```bash
fm apps add mybench erpnext:version-15
fm apps add mybench/all hrms:version-15
fm apps list mybench
```

### :material-web: [`fm domain`](domain.md) {.command-heading}
**Manage alias domains**

Add, remove, or list a bench's alias domains. A new alias has no certificate until you run `fm ssl add`.

```bash
fm domain add mybench www.example.com
fm domain remove mybench/www.example.com
fm domain list mybench
```

### :material-toolbox: [`fm tools`](tools.md) {.command-heading}
**Manage admin tools**

Start or stop a bench's Adminer and Mailpit containers, or route a single site to the tools already running.

```bash
fm tools enable mybench
fm tools disable mybench
fm tools status mybench
```

### :material-chart-line: [`fm telemetry`](telemetry.md) {.command-heading}
**Manage APM monitoring**

Turn New Relic reporting on or off for a bench and report whether it is actually reporting. The license key is stored once and reused; the agent's own config file is seeded once and then yours.

```bash
fm telemetry enable mybench newrelic --license-key YOUR_INGEST_KEY
fm telemetry status mybench
fm telemetry disable mybench newrelic
```

### :material-restore: [`fm reset`](reset.md) {.command-heading}
**Reset a bench**

Drop the database and reinstall all apps from scratch. Destructive operation.

```bash
fm reset mybench
```

### :material-tunnel: [`fm ngrok`](ngrok.md) {.command-heading}
**Create ngrok tunnel**

Expose a local bench to the internet via ngrok for webhooks, mobile testing, or demos.

```bash
fm ngrok mybench
```

## Access Control

Protect a bench with HTTP basic auth, or take it into maintenance.

### :material-lock: [`fm auth`](auth.md) {.command-heading}
**Password-protect a bench**

Put an HTTP basic auth prompt in front of the site, the admin tools, or both; `--status` reports the current state without changing it.

```bash
fm auth mybench --protect web
fm auth mybench --protect tools
fm auth mybench --status
```

### :material-traffic-cone: [`fm maintenance`](maintenance.md) {.command-heading}
**Put a bench into maintenance**

Serve a maintenance page for a bench's domains, aliases included, with IP/path allow lists and a bypass URL; `--off` brings the real site back.

```bash
fm maintenance mybench
fm maintenance mybench --allow-ip 203.0.113.7
fm maintenance mybench --off
```

---

## Deployment

Bake immutable images and ship them by switching a bench onto an image. See the [Deployment guide](../deploy/index.md) for the full workflow.

### :material-image-multiple: [`fm bake`](bake.md) {.command-heading}
**Bake an immutable app image**

Provision a bench's apps into a runtime image, or build standalone from `--apps`/`--config` for CI pipelines. `--image` names the app image produced and takes a full ref, so a pipeline names the tag it is about to ship; a bare repo gets a generated `:<timestamp>-<sha>` tag instead. `--base-image` names what that image is built from.

```bash
fm bake mybench
fm bake mybench --image ghcr.io/acme/mysite:v42 --push
fm bake --apps erpnext:version-15 --image ghcr.io/acme/mysite --push
```

### :material-swap-horizontal: [`fm switch`](switch.md) {.command-heading}
**Switch to an image, or roll back**

Forward deploys and rollbacks are the same pipeline pointed at different images. `--previous` rolls back with migrate disabled; add `--restore-db` to restore the deploy's DB dump too.

```bash
fm switch mybench local/mybench:20260721-abc123
fm switch mybench --previous
fm switch mybench --previous --restore-db
```

### :material-broom: [`fm prune`](prune.md) {.command-heading}
**Reclaim a bench's disk: releases, backups, logs**

Three categories, all by default: old deploy releases (history, dumps, images; `keep_releases` or `--keep-releases`), old backup sessions, and log rotation for files over the threshold. Retention in the `[prune]` config table; the host tier has `fm services prune`.

```bash
fm prune mybench --dry-run
fm prune mybench --only logs
fm prune mybench --only releases --keep-releases 3
```

---

## SSL & Security

Manage SSL certificates and HTTPS.

### :material-certificate: [`fm ssl`](ssl.md) {.command-heading}
**Manage SSL certificates**

Add, remove, renew, and list SSL certificates using Let's Encrypt (HTTP-01 or DNS-01 challenges), or by importing your own certificate with `--custom`.

```bash
fm ssl add mybench/example.com
fm ssl add mybench/example.com --challenge dns01
fm ssl add mybench/example.com --custom --cert ./tls.crt --key ./tls.key
fm ssl remove mybench/example.com
fm ssl renew mybench/example.com
fm ssl list mybench
```

---

## Global Services

Manage shared services used by all benches.

### :material-server: [`fm services`](services.md) {.command-heading}
**Manage global services**

Start, stop, restart, or shell into the shared `mariadb` and `nginx-proxy` containers. `fm services info` shows their status and the root DB credentials; `fm services real-ip` configures the trusted CDN/proxy so real client IPs reach fm and Frappe; `fm services migrate` and `fm services prune` are this tier's counterparts to `fm migrate` and `fm prune`.

```bash
fm services start mariadb
fm services stop all
fm services restart nginx-proxy
fm services shell mariadb
fm services info
fm services real-ip --cdn cloudflare
```

---

## Maintenance

System-level operations and updates.

### :material-database-refresh: [`fm migrate`](migrate.md) {.command-heading}
**Bring benches up to the current version**

Benches only, with automatic backups and rollback on failure; fm's own shared services and configuration are migrated separately with `fm services migrate` (see [Global Services](#global-services)), and `fm migrate` refuses to run while that half is behind.

```bash
fm migrate
fm migrate all
```

### :material-wrench: [`fm self`](self.md) {.command-heading}
**Manage the tool itself**

Update `fm`, pull latest Docker images, or stop everything FM manages.

```bash
fm self upgrade
fm self update-images
fm self stop
```

---

!!! tip "Quick Help"
    Use `fm <command> --help` to see detailed options and examples for any command.
