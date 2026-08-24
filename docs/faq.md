# FAQ

## Getting Started

### How do I install Frappe Manager?

```bash
uv tool install --python 3.13 frappe-manager
```

`uv` owns that command, not fm. For pipx, uvx, dev builds, and the prerequisites, see the [Installation guide](getting-started/installation.md).

### How do I update FM itself?

```bash
fm self update
fm migrate --all-benches
```

The first updates the CLI, the second brings fm's config, the global services and your benches up to match it. Every bench command refuses to run against a bench that is behind, so do not skip it. Details: [Upgrading fm](getting-started/installation.md#upgrading-fm).

### Can I run multiple benches on the same machine?

Yes. Each bench gets its own containers, workspace, apps and database schema. Two things are shared: one MariaDB server (`global-db`) and one nginx proxy holding ports 80 and 443, which routes to the right bench by domain. `fm list` shows them all.

---

## Working with Benches

### How do I install ERPNext?

At create time, `fm create mybench --apps erpnext`. On an existing `mount` bench, `fm update mybench --apps erpnext`. Refs, private repos and monorepo layouts: [App Management](guides/app-management.md).

### How do I check installed apps and versions?

```bash
fm info mybench
```

### How do I use a private GitHub repo for an app?

Pass `--github-token YOUR_TOKEN` to `fm create`, or export `GITHUB_TOKEN` before running it. fm stores the token in the bench config, so a later `fm update --apps` reuses it; `fm update` has no `--github-token` flag of its own. Either way fm checks the repo and ref with `git ls-remote` before it starts building, so a bad token fails early. See [App Management](guides/app-management.md).

### How do I change the Administrator password?

Without losing data, using Frappe's own `bench` command inside the container:

```bash
fm shell mybench -c "bench set-admin-password 'a-better-password'"
```

`fm reset` also takes `--admin-pass`, but it reinstalls the site from scratch and destroys everything in it.

### How do I reset a bench to a clean state?

`fm reset mybench` drops the site's database and reinstalls every app. All site data is lost, so back up first. It only works on the database server fm owns: a bench with its own `[database]` config entry is refused, because that schema is not fm's to drop. See [fm reset](commands/reset.md).

### How do I back up my bench?

Backups are Frappe's job; fm just runs the command for you. `bench backup --with-files` is a Frappe flag, not an fm one:

```bash
fm shell mybench -c "bench backup --with-files"
```

Where the files land, scheduled backups, and restoring: [Backup & Restore](guides/backup-restore.md).

### How do I run bench commands like migrate or build?

`fm shell` puts you in the bench container, where the `bench` CLI lives. fm never wraps `bench` subcommands; you call them yourself.

```bash
fm shell mybench                      # interactive shell
fm shell mybench -c "bench migrate"   # one command, its exit code becomes fm's
fm shell mybench -- bench build       # same thing, everything after -- is the command
```

`fm shell mybench --bench-console` gets you a Frappe console with `frappe` already initialised and connected.

### How do I share my bench for testing?

Create a temporary public URL with ngrok. An auth token is required; sign up at [ngrok.com](https://ngrok.com) if you don't have one.

```bash
fm ngrok mybench --auth-token YOUR_TOKEN --save-token
```

`--save-token` writes it to fm's config so later runs are just `fm ngrok mybench`. Without either `--save-token` or `--no-save-token`, fm asks. `NGROK_AUTHTOKEN` works too.

For a stable custom domain instead of a temporary URL, see [Domains](guides/domains.md).

---

## Workers & Restarts

### How do I restart just the web server or just the workers?

`fm restart mybench --no-workers` for web only, `fm restart mybench --no-web` for workers only, or `--service NAME` (repeatable) for one named service such as `socketio`, `nginx`, or a single worker. Inside the container, `fmx` gives finer control. See [fm restart](commands/restart.md) and the [fmx guide](guides/fmx.md).

### How do I safely restart during a deployment without losing jobs?

`fm restart mybench` already does this. Workers drain by default: fm waits for in-flight RQ jobs, and if one outlasts `[workers].drain_timeout` it **aborts the restart** rather than kill the job. `--no-drain` skips the wait and interrupts running jobs (they land in the failed-jobs registry); `--force` kills everything fast. Note that `--service` skips the drain. See [fm restart](commands/restart.md) and the [fmx guide](guides/fmx.md) for drain plus migrate and maintenance mode.

---

## Troubleshooting

### My site won't load at mybench.localhost: what should I check?

Work through these in order:

- Docker is running: `docker ps`
- The bench is listed and running: `fm list`
- The global nginx proxy came up. It needs ports 80 and 443 on the host, so anything else already bound there will have stopped it: `fm services start`
- On Windows 10, `*.localhost` may not resolve; add a `hosts` entry as described in the [WSL guide](guides/wsl.md)
- Still stuck? Read the log: `fm logs mybench -f`, then [Reading logs](reference/logs.md)

### Docker images fail to pull from GHCR. What can I try?

fm's images are public on `ghcr.io`, so no login is needed. A stale or expired GHCR credential left in your Docker keychain is the usual cause; drop it and pull again:

```bash
docker logout ghcr.io
fm self update-images
```

If pulls still fail, check the CLI logs for details: [Logs](reference/logs.md).

### How do I enable HTTPS for my bench?

```bash
fm ssl add mybench example.com
```

The default HTTP-01 challenge needs the domain's A record pointing at this server and port 80 reachable from the internet. If you cannot open port 80, use `--challenge dns01` with saved provider credentials. For a local bench that needs no public DNS at all, `--dev` issues from fm's own CA.

Renewal is not automatic: run `fm ssl renew --all` from a daily cron. The [SSL guide](guides/ssl.md) has step-by-step instructions for all of it.
