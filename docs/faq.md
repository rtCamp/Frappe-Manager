# FAQ

## Getting Started

### How do I install Frappe Manager?

See the [Installation guide](getting-started/installation.md). The recommended method:

```bash
uv tool install --python 3.13 frappe-manager
```

### How do I update FM itself?

Run these two commands. The first updates the CLI; the second updates your benches and infrastructure to match.

```bash
fm self update
fm migrate --all-benches
```

Details: [Upgrading fm](getting-started/installation.md#upgrading-fm).

### Can I run multiple benches on the same machine?

Yes. Each bench is fully isolated. Create as many as you need and list them any time:

```bash
fm list
```

---

## Working with Benches

### How do I install ERPNext?

At create time: `fm create mybench --apps erpnext`. For existing benches and everything else, see [App Management](guides/app-management.md).

### How do I check installed apps and versions?

```bash
fm info mybench
```

### How do I use a private GitHub repo for an app?

Pass `--github-token YOUR_TOKEN` at create time, or export `GITHUB_TOKEN` in your shell before running `fm create`. See [App Management](guides/app-management.md).

### How do I change the Administrator password?

**Option A** - reset and reinstall (destructive):

```bash
fm reset mybench --admin-pass newpass
```

**Option B** - change password only, no data loss:

```bash
fm shell mybench -c "bench set-admin-password newpass"
```

### How do I reset a bench to a clean state?

`fm reset mybench` drops the bench database and reinstalls all apps from scratch. This is destructive - back up first. See [fm reset](commands/reset.md).

### How do I back up my bench?

Run `fm shell mybench -c "bench backup --with-files"`, or use the Frappe web UI. See [Backup & Restore](guides/backup-restore.md).

### How do I run bench commands like migrate or build?

Use `fm shell` to open an interactive shell or run a single command:

```bash
fm shell mybench -c "bench migrate"
fm shell mybench          # interactive
```

### How do I share my bench for testing?

Create a temporary public URL with ngrok. An auth token is required - sign up at [ngrok.com](https://ngrok.com) if you don't have one.

```bash
fm ngrok mybench --auth-token YOUR_TOKEN
```

Once a token is saved, future runs don't need it:

```bash
fm ngrok mybench
```

For a stable custom domain instead of a temporary URL, see [Domains](guides/domains.md).

---

## Workers & Restarts

### How do I restart just the web server or just the workers?

From the host: `fm restart mybench --no-workers` (web only), `fm restart mybench --no-web` (workers only), or `--service NAME` for one specific service. Inside the container, `fmx` gives the same control over individual supervisor processes. See [fm restart](commands/restart.md) and the [fmx guide](guides/fmx.md).

### How do I safely restart during a deployment without losing jobs?

Use `fm restart mybench --drain` - workers finish their current jobs before anything restarts. See [fm restart](commands/restart.md) and the [fmx guide](guides/fmx.md) for drain plus migrate and maintenance mode.

---

## Troubleshooting

### My site won't load at mybench.localhost: what should I check?

Work through these in order:

- Docker is running: `docker ps`
- The bench is listed and running: `fm list`
- Ports 80 and 443 are free on the host (nothing else bound to them)
- On Windows 10, add `127.0.0.1 mybench.localhost` to your `hosts` file if the `.localhost` domain doesn't resolve
- Still stuck? Check the logs: [Reading logs](reference/logs.md)

### Docker images fail to pull from GHCR. What can I try?

Log out and back in to the GitHub Container Registry:

```bash
docker logout ghcr.io
docker login ghcr.io
```

If pulls still fail, check the CLI logs for details: [Logs](reference/logs.md).

### How do I enable HTTPS for my bench?

SSL requires that your domain points at the server and that ports 80 and 443 are reachable from the internet.

```bash
fm ssl add mybench example.com
```

If you cannot open port 80 (for example on a corporate network), use DNS-01 challenge with Cloudflare instead. See the [SSL guide](guides/ssl.md) for step-by-step instructions.
