# Architecture

This page explains what runs on your machine when you use Frappe Manager.

## Workspace layout

By default everything lives under `~/frappe/`. Set the `FRAPPE_MANAGER_HOME` environment variable to change the root directory.

- `~/frappe/fm_config.toml` — global FM settings (ngrok token, SSL provider, etc.)
- `~/frappe/sites/` — one folder per bench. Each bench contains `bench_config.toml`, compose files, and `workspace/`.
- `~/frappe/services/` — global services (shared MariaDB and nginx-proxy).
- `~/frappe/backups/` — migration and manual backup dumps.
- `~/frappe/archived/` — benches moved here by FM when something goes wrong during creation.
- `~/frappe/logs/` — FM CLI log files.

## Service tiers

### Global services (shared by all benches)

| Service | Image | Description |
|---|---|---|
| `global-db` | `mariadb:10.6` | MariaDB database used by all benches |
| `global-nginx-proxy` | `jwilder/nginx-proxy:1.6` | Reverse proxy listening on ports 80 and 443 |

!!! note "macOS vs Linux — database storage"
    On **Linux** the MariaDB data directory is bind-mounted from `~/frappe/services/mariadb/data/` on the host.
    On **macOS** the data is stored in a named Docker volume (`fm-global-db-data`) to avoid performance issues with bind mounts on macOS.

### Per-bench services

Each bench is a separate Docker Compose project with its own set of containers.

| Service | Image | Description |
|---|---|---|
| `frappe` | `ghcr.io/rtcamp/frappe-manager-frappe:<tag>` | Frappe application (gunicorn / supervisor) |
| `nginx` | `ghcr.io/rtcamp/frappe-manager-nginx:<tag>` | Per-bench nginx |
| `socketio` | `ghcr.io/rtcamp/frappe-manager-frappe:<tag>` | Socket.IO server |
| `schedule` | `ghcr.io/rtcamp/frappe-manager-frappe:<tag>` | Frappe scheduler |
| `redis-cache` | `redis:8-alpine` | Redis for caching |
| `redis-queue` | `redis:8-alpine` | Redis for background jobs |
| Workers | `ghcr.io/rtcamp/frappe-manager-frappe:<tag>` | Short, long, and app-defined workers (separate compose file) |

### Admin tools (optional, per bench)

| Service | Image | Description |
|---|---|---|
| `mailpit` | `axllent/mailpit:v1.22` | Email catcher for development |
| `adminer` | `adminer:4` | Database web UI |

Admin tools live in their own compose file (`docker-compose.admin-tools.yml`) and are only started when enabled.

## Compose files

Each bench uses multiple compose files stacked together:

| File | Purpose |
|---|---|
| `docker-compose.yml` | Core bench services (frappe, nginx, socketio, schedule, redis) |
| `docker-compose.workers.yml` | Worker containers (generated based on configured workers) |
| `docker-compose.admin-tools.yml` | Mailpit and Adminer (only when admin tools are enabled) |

## Container naming

Container names follow the pattern `fm__{bench-name}__{service}`, where dots in the bench name are replaced with `__`.

Example: bench `mybench` → containers `fm__mybench__frappe`, `fm__mybench__nginx`, `fm__mybench__mailpit`, etc.

## Volumes

- **`fm-sockets`** — shared Unix socket volume used by [`fmx`](../guides/fmx.md) to communicate with supervisord processes inside the frappe container. Each service (frappe, short-worker, long-worker, schedule, socketio) exposes a socket at `/fm-sockets/{service}.sock`.
- **`mailpit-data`** — per-bench volume storing Mailpit's email database (named `fm__{bench}__mailpit-data`).
- Per-bench workspace bind-mount at `~/frappe/sites/{bench}/workspace/`.

## Networks

| Network | Description |
|---|---|
| `fm-global-frontend-network` | Connects per-bench nginx containers to the global nginx-proxy |
| `fm-global-backend-network` | Connects per-bench services to global-db |
| `fm__{bench}__site-network` | Connects all containers within a single bench |

## Logs

- FM CLI logs: `~/frappe/logs/`
- Frappe application logs: `~/frappe/sites/{bench}/workspace/frappe-bench/logs/`
- Container stdout/stderr: accessible via `fm logs mybench`
