# Commands

Every `fm` command has a page here with its arguments, options and worked examples. `fm <command> --help` prints the same option list in the terminal.

<div class="grid cards" markdown>

-   :material-plus-circle:{ .lg .middle } **[Create a bench](create.md)**

    ---

    ```bash
    fm create mybench
    fm create mybench --apps erpnext
    ```

-   :material-play-circle:{ .lg .middle } **[Start and stop](start.md)**

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

-   :material-certificate:{ .lg .middle } **[Add HTTPS](ssl.md)**

    ---

    ```bash
    fm ssl add mybench/example.com
    fm ssl add mybench/example.com --challenge dns01
    ```

</div>

## Global options

These come before the subcommand and work everywhere.

| Option | Default | Description |
|---|---|---|
| `-v`, `--verbose` | off | Info-level output |
| `--log-level TEXT` | from config | Set the level explicitly: `debug`, `info`, `warning`, `error` |
| `-n`, `--non-interactive` | off | Never prompt. A prompt that would have been shown becomes an error naming the flag that answers it |
| `--json` | off | Write every output event to stdout as one JSON line (JSONL) |
| `-V`, `--version` | | Print the fm version and exit |

## Flag conventions

Five rules hold across every command, so a flag means the same thing everywhere.

**`--yes` / `-y` answers confirmation prompts.** It means "do the thing I typed, don't ask". It never expands what a command does. Every prompt defaults to No, so a bare Enter aborts. Under `--non-interactive`, an unanswered prompt refuses and names `--yes`.

**A decision that changes what happens gets its own named flag.** `--restore-db`, `--delete-backups`, `--skip-db-backup`, `--on-failure`. Such a choice is never buried in a prompt alone, and `--yes` never answers it for you.

**`--force` selects a stronger action**, such as recreating containers, interrupting jobs, or renewing a certificate early. It never skips a question.

**`--dry-run` prints the plan and changes nothing.** Exit 0, never prompts. Available on the plan-first commands: `prune`, `services prune`, `migrate`, `services migrate`, `delete`, `update`, `create`. It is the scriptable way to see a plan, because a non-interactive run without `--yes` refuses instead.

**The exit code answers "did the thing I typed happen?"** Declining a confirmation exits non-zero, because the command was asked to act and did not. That is the same answer a non-interactive run without `--yes` gives, so a script cannot tell a human saying no from a refused flag, and neither reads as success. A command that finds nothing to do, such as `fm migrate` with no stale benches, exits 0: that one did finish.

## Bench lifecycle

| Command | What it does |
|---|---|
| [`fm create`](create.md) | Build a new bench and start it |
| [`fm start`](start.md) | Bring a stopped bench's containers up |
| [`fm stop`](stop.md) | Shut a bench's containers down, keeping all data |
| [`fm restart`](restart.md) | Restart web and workers through supervisor, whole containers, or a zero-downtime rolling swap |
| [`fm delete`](delete.md) | Remove a bench directory, and optionally its database |
| [`fm reset`](reset.md) | Drop the database and reinstall every app from scratch |
| [`fm list`](list.md) | Every bench with its status, runtime and apps |
| [`fm info`](info.md) | One bench's URL, credentials, apps and service state |

## Working on a bench

| Command | What it does |
|---|---|
| [`fm shell`](shell.md) | A shell, a single command, or the Frappe console inside the bench |
| [`fm code`](code.md) | Open the bench in VS Code attached to its container |
| [`fm logs`](logs.md) | Show or follow a bench service's log |
| [`fm compose`](compose.md) | Raw `docker compose` against a bench, with its compose files already wired up |

## Changing a bench

| Command | What it does |
|---|---|
| [`fm update`](update.md) | Change environment, runtime, Python or Node version, restart policy, upload limit, database and Redis endpoints. Plan-first |
| [`fm apps`](apps.md) | Fetch an app onto a bench, install it into one site or all, and list what is installed |
| [`fm domain`](domain.md) | Add, remove and list a bench's alias domains |
| [`fm tools`](tools.md) | Start, stop and inspect the bench's Adminer and Mailpit containers |
| [`fm telemetry`](telemetry.md) | Turn New Relic reporting on or off, and report whether it is reporting |
| [`fm ngrok`](ngrok.md) | Expose a local bench to the internet through an ngrok tunnel |

## Access control

| Command | What it does |
|---|---|
| [`fm auth`](auth.md) | Put HTTP basic auth in front of the site, the admin tools, or both |
| [`fm maintenance`](maintenance.md) | Serve a maintenance page for a bench's domains, with allow lists and a bypass URL |
| [`fm ssl`](ssl.md) | Issue, import, renew, list and remove certificates; manage DNS credentials and the dev CA |

## Deployment

| Command | What it does |
|---|---|
| [`fm bake`](bake.md) | Provision a bench's apps into an immutable image, from a bench or standalone in CI |
| [`fm switch`](switch.md) | Point a bench at an image, or roll back to the previous one |
| [`fm prune`](prune.md) | Reclaim a bench's disk: old releases, old backup sessions, oversized logs |

The full workflow is in the [Deployment guide](../deploy/index.md).

## Host and shared services

| Command | What it does |
|---|---|
| [`fm services`](services.md) | Manage the shared MariaDB and nginx proxy: start, stop, shell, info, real-ip, migrate, prune |
| [`fm migrate`](migrate.md) | Bring benches up to the installed fm version, with backups and rollback |
| [`fm self`](self.md) | Upgrade fm, pull images, stop everything fm manages, or uninstall it |
