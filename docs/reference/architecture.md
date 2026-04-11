# Architecture

This page explains what runs on your machine when you use Frappe Manager.

Workspace layout (default `~/frappe/`):

- `~/frappe/services/` — globally shared services such as the database and nginx proxy.
- `~/frappe/sites/` — bench workspaces and site folders.
- `~/frappe/logs/` — CLI and service logs.
- `~/frappe/migration/` — migration helper files.

Services overview:

- Global services: `global-db` (MariaDB), `global-nginx-proxy` (proxy for HTTP/HTTPS).
- Bench services: each bench has its own compose project with services like `frappe` (web), `redis-cache`, `redis-queue`, `socketio`, `workers`, and `nginx` (per-bench). 

CLI logs: `~/frappe/logs/fm.log` with rotation (10MB per file, 3 rotations by default).
