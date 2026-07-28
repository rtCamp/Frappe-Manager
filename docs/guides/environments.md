# Environments

Running a `dev` environment on a production server exposes debug tools, uses a single-threaded web server, and won't auto-restart after crashes. This guide shows you when and how to switch between `dev` (development) and `prod` (production) modes.

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
| **Restart on crash** | ❌ No (default policy `no`) | ✅ Yes (default policy `unless-stopped`) |
| **Hot-reload** | ✅ Assets + Python | ❌ Disabled |
| **Admin tools at create** | ✅ Mailpit, Adminer | ❌ Disabled |
| **Performance** | Slower (for DX) | Optimized for load |
| **Use for** | Local development | Staging, production servers |

---

## What actually changes

### 1. Web server process

**Development (`dev`):**

```bash
bench serve --port 80  # Werkzeug development server
bench watch            # Asset hot-reload watcher
```

Single-threaded. Changes to Python/JS/CSS reload automatically. Intended for one developer.

**Production (`prod`):**

```bash
gunicorn -b 0.0.0.0:80 -w <workers> --worker-class=gthread --threads <threads> --max-requests 1000 --preload frappe.app:application
```

Multi-worker WSGI server. The worker count defaults to the smaller of the CPU count and a RAM-based cap (one worker per 256 MB); threads default to 2–4 per worker. No auto-reload. See [Workers & Background Jobs](../reference/workers.md) to customize via `common_site_config.json`.

---

### 2. Restart policy

The Docker restart policy is a **per-bench setting** (`restart_policy` in `bench_config.toml`), applied to the `frappe` container, workers, and all bench services. The environment only picks the *default at create time*:

| Created as | Default policy | Behavior |
|-------------|----------------------|----------|
| `dev` | `no` | Containers **do not** restart after a crash or host reboot. Run `fm start mybench` manually. |
| `prod` | `unless-stopped` | Containers **auto-restart** after crashes or reboots, unless you explicitly stopped them with `fm stop`. |

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

| Environment | Default `developer_mode` |
|-------------|-------------------------|
| `dev` | **On** (`1`) |
| `prod` | **Off** (`0`) |

You can toggle `developer_mode` **independently** of the environment:

```bash
# Enable developer mode in production (not recommended)
fm update mybench --developer-mode enable

# Disable developer mode in development (rare)
fm update mybench --developer-mode disable
```

!!! info "Environment vs developer mode"
    **Environment** (`dev`/`prod`) controls the web server type and restart policy. **Developer mode** controls Frappe's debug features. They're related but independent — you can run prod environment with developer mode enabled, though this isn't recommended for production servers.

---

### 4. Admin tools (Mailpit & Adminer)

| Tool | Purpose | Dev default | Prod default | Access URL |
|------|---------|-------------|--------------|------------|
| **Mailpit** | Email testing (catches all outgoing emails) | ✅ Enabled | ❌ Disabled | `http://mybench.localhost/mailpit` |
| **Adminer** | Database web UI | ✅ Enabled | ❌ Disabled | `http://mybench.localhost/adminer` |

These are **create-time defaults** — switching environments later does not enable or disable admin tools.

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
| `prod` | **Removes** dev packages (keeps production image clean) |

Example dev dependencies: `pytest`, `black`, `mypy`, `ipdb`.

---

## When to use each environment

### Use `dev` when:

- ✅ Actively writing or debugging Frappe application code
- ✅ Need hot-reload for Python/JS/CSS changes
- ✅ Want Mailpit and Adminer without extra setup
- ✅ Bench runs on your **local machine**
- ✅ Single developer workflow

### Use `prod` when:

- ✅ Bench is **deployed on a server** and accessed by end users
- ✅ Need **multi-worker concurrency** for handling simultaneous requests
- ✅ Want **automatic recovery** from crashes and reboots
- ✅ Running **performance benchmarks** or load tests
- ✅ Security matters (no debug tools exposed)

!!! tip "Staging servers"
    For staging environments that mirror production, use `prod` mode with `--developer-mode enable` if you need detailed error tracebacks during testing.

---

## Switching guide: Dev to production checklist

When deploying a bench to production, follow this sequence:

1. **Switch environment:**
   ```bash
   fm update mybench --environment prod
   ```

2. **Disable developer mode** (if not already disabled):
   ```bash
   fm update mybench --developer-mode disable
   ```

3. **Set the restart policy** (env switch keeps the old one):
   ```bash
   fm update mybench --restart unless-stopped
   ```

4. **Disable admin tools** (they stay enabled from the dev defaults):
   ```bash
   fm update mybench --admin-tools disable
   ```

5. **Remove dev packages:**
   ```bash
   fm stop mybench
   fm start mybench --sync-dev-packages
   ```

6. **Set up SSL** (if not already done):
   ```bash
   fm ssl add mybench yourdomain.com
   ```
   See [SSL Guide](ssl.md) for details.

7. **Configure automated renewals:**
   ```bash
   # Add to crontab
   0 3 * * * fm ssl renew --all
   ```

8. **Verify the bench is running:**
   ```bash
   fm list
   curl -I https://yourdomain.com
   ```

---

## Reference: All environment-related commands

```bash
# Create with specific environment
fm create mybench --environment dev
fm create mybench --environment prod

# Switch environment
fm update mybench --environment prod
fm update mybench --environment dev

# Toggle developer mode independently
fm update mybench --developer-mode enable
fm update mybench --developer-mode disable

# Toggle admin tools independently
fm update mybench --admin-tools enable
fm update mybench --admin-tools disable

# Set the Docker restart policy
fm update mybench --restart unless-stopped

# Sync dev packages
fm start mybench --sync-dev-packages

# Check current environment
fm info mybench
```

---

!!! info "See also"
    - [VSCode Integration](vscode.md) — attach debugger to dev benches
    - [Deployment — Image Benches](deployment.md) — ship production code as immutable images
    - [Admin Tools](admin-tools.md) — Mailpit and Adminer details
    - [Workers & Background Jobs](../reference/workers.md) — customize Gunicorn workers
    - [SSL Guide](ssl.md) — secure production benches with HTTPS
