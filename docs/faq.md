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

### Can I run multiple benches on the same machine?

Yes. Each bench is fully isolated. Create as many as you need and list them any time:

```bash
fm list
```

### What happened to pyenv and nvm?

In v0.19.0 FM switched to `uv` for Python and `fnm` for Node. After upgrading, run:

```bash
fm migrate mybench
```

---

## Working with Benches

### How do I install ERPNext?

At create time:

```bash
fm create mybench --apps erpnext
```

On an existing bench:

```bash
fm shell mybench -c "bench get-app erpnext && bench --site mybench.localhost install-app erpnext"
```

### How do I check installed apps and versions?

```bash
fm info mybench
```

### How do I use a private GitHub repo for an app?

Provide the repo and token at create time:

```bash
fm create mybench --apps org/private-app:main --github-token YOUR_TOKEN
```

Or export `GITHUB_TOKEN` in your shell before running `fm create` and FM will pick it up automatically.

### How do I change the Administrator password?

**Option A** — reset and reinstall (destructive):

```bash
fm reset mybench --admin-pass newpass
```

**Option B** — change password only, no data loss:

```bash
fm shell mybench -c "bench set-admin-password newpass"
```

### How do I reset a bench to a clean state?

`fm reset mybench` drops the bench database and reinstalls all apps from scratch. This is destructive — back up first.

### How do I back up my bench?

From the Frappe web UI: **Setup → Download Backup**.

From the command line:

```bash
fm shell mybench -c "bench backup --with-files"
```

### How do I run bench commands like migrate or build?

Use `fm shell` to open an interactive shell or run a single command:

```bash
fm shell mybench -c "bench migrate"
fm shell mybench          # interactive
```

### How do I share my bench for testing?

Create a temporary public URL with ngrok. An auth token is required — sign up at [ngrok.com](https://ngrok.com) if you don't have one.

```bash
fm ngrok mybench --auth-token YOUR_TOKEN
```

Once a token is saved, future runs don't need it:

```bash
fm ngrok mybench
```

---

## Workers & Restarts

### How do I restart just the web server or just the workers?

From the host, `fm restart` takes group flags and a `--service` selector:

```bash
# Web only (frappe + socketio)
fm restart mybench --no-workers

# Workers only (schedule + all workers)
fm restart mybench --no-web

# One specific service (repeatable)
fm restart mybench --service short-worker --service long-worker
```

Inside the container, [`fmx`](guides/fmx.md) gives the same control over individual supervisor processes:

```bash
fm shell mybench -c "fmx status"
fm shell mybench -c "fmx restart frappe"
```

### How do I safely restart during a deployment without losing jobs?

Drain the queues first — workers finish their current jobs before anything restarts:

```bash
fm restart mybench --drain
```

To also run `bench migrate` as part of the same step, use fmx inside the container:

```bash
fm shell mybench -c "fmx restart --drain-workers --migrate"
```

See the [fmx guide](guides/fmx.md) for the full reference including maintenance mode.

---

## Troubleshooting

### My site won't load at mybench.localhost — what should I check?

Work through these in order:

- Docker is running: `docker ps`
- The bench is listed and running: `fm list`
- Ports 80 and 443 are free on the host (nothing else bound to them)
- On Windows 10, add `127.0.0.1 mybench.localhost` to your `hosts` file if the `.localhost` domain doesn't resolve

### Docker images fail to pull from GHCR. What can I try?

Log out and back in to the GitHub Container Registry:

```bash
docker logout ghcr.io
docker login ghcr.io
```

### How do I enable HTTPS for my bench?

SSL requires that your domain points at the server and that ports 80 and 443 are reachable from the internet.

```bash
fm ssl add mybench example.com
```

If you cannot open port 80 (for example on a corporate network), use DNS-01 challenge with Cloudflare instead. See the [SSL guide](guides/ssl.md) for step-by-step instructions.
