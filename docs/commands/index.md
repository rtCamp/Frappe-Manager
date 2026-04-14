# Command Reference

Complete reference for all `fm` CLI commands. Each command page includes usage, options, and real-world examples.

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
    fm ssl add mybench example.com
    fm ssl add mybench example.com --challenge dns01
    ```

</div>

---

## Bench Lifecycle

Core commands for creating and managing benches.

### :material-plus-circle: [`fm create`](create.md) {.command-heading}
**Create a new bench with apps**

Set up a fresh Frappe development or production environment with your choice of apps, Python/Node versions, and configuration.

```bash
fm create mybench
fm create mybench --apps erpnext --apps hrms
fm create mybench -e prod
```

### :material-play-circle: [`fm start`](start.md) {.command-heading}
**Start a bench**

Brings up all containers and services for a stopped bench.

```bash
fm start mybench
fm start mybench --container-logs
```

### :material-stop-circle: [`fm stop`](stop.md) {.command-heading}
**Stop a bench**

Shuts down all containers without removing any data.

```bash
fm stop mybench
```

### :material-restart: [`fm restart`](restart.md) {.command-heading}
**Restart bench services**

Restart web, workers, or specific services using supervisor or container restart.

```bash
fm restart mybench
fm restart mybench --supervisor
fm restart mybench --workers --no-web
```

### :material-delete: [`fm delete`](delete.md) {.command-heading}
**Delete a bench**

Permanently removes a bench directory and optionally its database from global-db.

```bash
fm delete mybench
fm delete mybench --delete-db-from-global-db
```

### :material-format-list-bulleted: [`fm list`](list.md) {.command-heading}
**Show all benches**

List all benches with their status and basic info.

```bash
fm list
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
fm code mybench --stop-before
```

### :material-text-box: [`fm logs`](logs.md) {.command-heading}
**View bench logs**

Stream or display logs from bench services (frappe, nginx, redis, etc.).

```bash
fm logs mybench
fm logs mybench -f
fm logs mybench --service nginx
```

### :material-information: [`fm info`](info.md) {.command-heading}
**Show bench details**

Display comprehensive bench configuration, status, installed apps, and environment info.

```bash
fm info mybench
fm info mybench --verbose
```

---

## Configuration

Modify bench settings and infrastructure.

### :material-cog: [`fm update`](update.md) {.command-heading}
**Update bench configuration**

Change environment type, Python/Node versions, enable/disable admin tools, manage alias domains, and more.

```bash
fm update mybench -e prod
fm update mybench --admin-tools enable
fm update mybench --python 3.11 --node 20
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

---

## SSL & Security

Manage SSL certificates and HTTPS.

### :material-certificate: [`fm ssl`](ssl.md) {.command-heading}
**Manage SSL certificates**

Add, remove, renew, and list SSL certificates using Let's Encrypt (HTTP-01 or DNS-01 challenges).

```bash
fm ssl add mybench example.com
fm ssl add mybench example.com --challenge dns01
fm ssl remove mybench example.com
fm ssl renew mybench example.com
fm ssl list mybench
```

---

## Global Services

Manage shared services used by all benches.

### :material-server: [`fm services`](services.md) {.command-heading}
**Manage global services**

Start, stop, restart, or shell into global services like `global-db` and `global-nginx-proxy`.

```bash
fm services start global-db
fm services stop all
fm services restart global-nginx-proxy
fm services shell global-db
```

---

## Maintenance

System-level operations and updates.

### :material-database-refresh: [`fm migrate`](migrate.md) {.command-heading}
**Run fm migrations**

Apply database or configuration migrations when upgrading Frappe Manager versions.

```bash
fm migrate
fm migrate --skip-backup
```

### :material-wrench: [`fm self`](self.md) {.command-heading}
**Manage the tool itself**

Update `fm`, pull latest Docker images, or run raw docker-compose commands on benches.

```bash
fm self update
fm self update-images
fm self compose mybench ps
```

---

!!! tip "Quick Help"
    Use `fm <command> --help` to see detailed options and examples for any command.
