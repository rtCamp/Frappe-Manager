# Architecture

This page explains what runs on your machine when you use Frappe Manager.

Workspace layout (default `~/frappe/`):

- `~/frappe/fm_config.toml` — global FM settings.
- `~/frappe/sites/` — one folder per bench (each bench has bench_config.toml, compose files, and workspace/).
- `~/frappe/services/` — global services (global MariaDB and nginx-proxy).
- `~/frappe/backups/` — migration and backup dumps.
- `~/frappe/archived/` — benches archived by fm on failure.

Service tiers

- Global services (shared by all benches):
  - `global-db` — MariaDB 10.6 used by all benches.
  - `global-nginx-proxy` — nginx reverse proxy listening on ports 80 and 443.

- Per-bench services (each bench has its own compose project):
  - `frappe` — the Frappe application (gunicorn / supervisor).
  - `nginx` — per-bench nginx.
  - `socketio` — Socket.IO server.
  - `schedule` — Frappe scheduler.
  - `redis-cache` and `redis-queue` — Redis services.
  - Worker containers (short-worker, long-worker, and app-defined workers).

Networks

- Docker networks used include `fm-global-frontend-network`, `fm-global-backend-network`, and a per-site network connecting a bench's containers.

Logs

- FM CLI logs and service logs are kept under `~/frappe/logs/` and inside each bench workspace under `workspace/frappe-bench/logs/`.
