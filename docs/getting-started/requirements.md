# Requirements

This page lists the things your computer needs for Frappe Manager to work. Each item includes why it is needed.

- Python 3.13+ — Frappe Manager is written in Python and uses Python features only available in recent versions.
- Docker Desktop (Mac/Windows) or Docker Engine (Linux) — containers run the database, web server, and other services that make your bench work.
- Non-root user with Docker access — running containers as a regular user is safer and matches how the tools expect file permissions.
- Ports 80 and 443 free — these are the standard HTTP and HTTPS ports used by the built-in proxy and TLS certificates. If they are busy, Frappe Manager cannot bind them.

!!! tip "Need help checking versions?"
    Run `python --version` and `docker --version` to confirm the installed versions.
