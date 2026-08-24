# Environments: Dev vs Prod

The environment is the **second axis** of a bench: it controls **how the web process runs** (`dev` uses Frappe's auto-reloading development server, `prod` uses Gunicorn), plus the convenience defaults that come with each (restart policy at create time, admin tools, developer mode).

!!! note "Runtime comes first"
    The environment does not decide where your code lives; that's the [runtime (mount vs image)](../concepts/runtimes.md). A `prod` bench can still be fully editable (`mount`), and an immutable `image` bench can run `dev`. Read [Concepts](../concepts/index.md) first if you haven't.

Running `dev` on a public server exposes debug tools, serves single-threaded, and won't restart after crashes. Switch such benches to `prod`.

## Switching environments

```bash
# Create a production bench
fm create mybench --environment prod

# Switch existing bench to production
fm update mybench --environment prod

# Switch back to development
fm update mybench --environment dev
```

!!! warning "What gets restarted"
    Only the `frappe` container is recreated when switching environments. Workers, nginx, and Redis stay running. The bench will be briefly unavailable during the switch.

---

## Quick comparison

| | `dev` | `prod` |
|---|---|---|
| **Web server** | Werkzeug (single-threaded) | Gunicorn (multi-worker) |
| **Restart on crash** | ❌ supervisord leaves the process down | ✅ supervisord restarts the web process |
| **Hot-reload** | ✅ Assets + Python | ❌ Disabled |
| **Admin tools at create** | ✅ Mailpit, Adminer | ❌ Disabled |
| **Performance** | Slower (for DX) | Optimized for load |
| **Use for** | Local development | Staging, production servers |

---

## What actually changes

### 1. Web server process

**Development (`dev`):** supervisord runs two Frappe bench processes.

```bash
bench serve --host 0.0.0.0 --port 80   # Werkzeug development server
bench watch                            # asset rebuild watcher
```

`--host` and `--port` are `bench serve`'s own options; fm supplies `0.0.0.0:80`, and falls back to `--port 80` alone on bench releases that do not accept `--host`. Single-threaded, one request at a time, and supervisord is told not to restart either process if it dies.

**Production (`prod`):** supervisord runs a generated wrapper, `workspace/frappe-bench/config/fm-web-server.sh`, which execs Gunicorn with the arguments fm computed:

```text
gunicorn -b 0.0.0.0:80 -w <workers> --worker-class=gthread --threads <threads>
         --max-requests 1000 --max-requests-jitter 100
         -t 120 --graceful-timeout 30
         frappe.app:application --preload
```

Every option on that line is **Gunicorn's own**, not an fm flag; fm only picks the values. It sizes `-w` from CPU count and RAM, `--threads` from CPU count, sets `--max-requests 1000` with a jitter of 10% of that, and takes `-t` from `http_timeout` (120 by default). See [Web Serving & Concurrency](../concepts/web-serving.md) for the sizing formula and the `common_site_config.json` keys that override each value.

!!! warning "`--preload` rules out an in-place code reload"
    fm always passes `--preload`, so Gunicorn imports `frappe.app` in the master process before forking workers. A HUP to the master therefore does not pick up changed Python, and restarting the web process takes the whole tier down for as long as the master needs to come back. Prod is not a place to edit code: ship it as an image with `fm bake` and `fm switch`, or work on a `dev` bench.

---

### 2. Restart policy

The Docker restart policy is a **per-bench setting** (`restart_policy` in `bench_config.toml`), applied to the `frappe` container, workers, and all bench services. The environment only picks the *default at create time*: a `dev` bench's containers stay down after a crash or host reboot (run `fm start mybench` manually), while a `prod` bench's containers auto-restart unless you explicitly stopped them with `fm stop`. Accepted values and defaults: [`restart_policy` reference](../reference/configuration.md#restart-policy).

Change it any time, independently of the environment:

```bash
fm update mybench --restart unless-stopped   # no | always | on-failure | unless-stopped
```

!!! warning "Switching environments does not change the restart policy"
    `fm update mybench --environment prod` switches the serving mode but keeps the bench's existing restart policy. A bench created as `dev` keeps policy `no` until you set `--restart unless-stopped` yourself. FM warns if you set policy `no` on a production bench.

---

### 3. Frappe developer mode

**Developer mode** is Frappe's built-in development flag (stored in `common_site_config.json`). It controls:

- Unminified JS/CSS assets (easier debugging)
- Full Python tracebacks in the browser
- Hot-reloading of Python changes without restart

It is on by default in `dev` benches and off in `prod` ones; see the [`developer_mode` reference](../reference/configuration.md#developer-mode).

You can toggle `developer_mode` **independently** of the environment:

```bash
# Enable developer mode in production (not recommended)
fm update mybench --developer-mode enable

# Disable developer mode in development (rare)
fm update mybench --developer-mode disable
```

`--developer-mode` is a mount-runtime option. An `image` bench bakes developer mode into the image, so `fm update` refuses the flag there; demote with `--runtime mount` first.

!!! info "Environment vs developer mode"
    **Environment** (`dev`/`prod`) controls the web server type and restart policy. **Developer mode** controls Frappe's debug features. They're related but independent: you can run prod environment with developer mode enabled, though this isn't recommended for production servers.

---

### 4. Admin tools (Mailpit & Adminer)

| Tool | Purpose | Access URL |
|------|---------|------------|
| **Mailpit** | Email testing (catches all outgoing emails) | `http://mybench.localhost/mailpit` |
| **Adminer** | Database web UI | `http://mybench.localhost/adminer` |

Both are enabled at create time on `dev` benches and disabled on `prod` ones. These are **create-time defaults** (switching environments later does not enable or disable admin tools); see the [`admin_tools` reference](../reference/configuration.md#admin-tools).

You can toggle admin tools **independently** of the environment:

```bash
fm update mybench --admin-tools enable
fm update mybench --admin-tools disable
```

See [Admin Tools](admin-tools.md) for details.

---

### 5. Logs

| Environment | Log file(s) |
|-------------|------------|
| `dev` | `logs/web.dev.log` (web server, combined) + `logs/watch.dev.log` (asset watcher) |
| `prod` | `logs/web.log` (stdout) + `logs/web.error.log` (stderr) |

Stream logs from the CLI:

```bash
# Show frappe server logs
fm logs mybench

# Follow logs in real-time
fm logs mybench --follow

# Logs from a specific container
fm logs mybench --service nginx
```

---

### 6. Dev packages

If your apps declare dev dependencies in `pyproject.toml` under `[tool.bench.dev-dependencies]`, you can sync them at start time:

```bash
fm start mybench --sync-dev-packages
```

| Environment | Effect |
|-------------|--------|
| `dev` | **Installs** dev packages |
| `prod` | **Removes** dev packages (keeps the venv lean) |

---

## When to use each environment

Use `dev` on your own machine, where you are editing app code and want the asset watcher, browser tracebacks and the admin tools. Use `prod` anywhere the bench is reachable by other people: multi-worker serving, containers that come back after a crash or reboot, and no debug surface.

!!! tip "Staging servers"
    For staging that mirrors production, run `prod` with `--developer-mode enable` if you need detailed error tracebacks during testing.

---

## Monitoring (New Relic)

Only the `prod` web process can report to New Relic APM: the agent is wired into the Gunicorn wrapper, and a `dev` bench's `bench serve` never sees it.

```bash
fm update mybench --newrelic --newrelic-license-key YOUR_INGEST_KEY
fm update mybench --no-newrelic
```

Enabling wraps the web process with the New Relic agent; fm force-recreates the frappe container to apply it. Works on both runtimes; it is a settings-level change. A license key is required the first time you enable it, after which `fm update mybench --newrelic` on its own reuses the stored one. The stored keys live under [`[monitoring.newrelic]`](../reference/configuration.md#monitoring-newrelic) in `bench_config.toml`.

---

!!! info "See also"
    - [VSCode Integration](vscode.md): attach debugger to dev benches
    - [Hosting on a Server](hosting.md): the full dev-to-prod go-live sequence
    - [Deployment (Image Benches)](../deploy/index.md): ship production code as immutable images
    - [Admin Tools](admin-tools.md): Mailpit and Adminer details
    - [Web Serving & Concurrency](../concepts/web-serving.md): Gunicorn workers and threads
    - [SSL Guide](ssl.md): secure production benches with HTTPS

