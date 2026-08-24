# Windows / WSL

fm runs on Windows through WSL 2. Install it inside the WSL distro exactly as you would on Linux (`uv tool install --python 3.13 frappe-manager`, or pipx) and use Docker Desktop's WSL 2 backend, with integration enabled for that distro so fm can reach the Docker socket.

Tips:

- Keep `~/frappe/` on the Linux filesystem (for example `/home/youruser/frappe`), not under `/mnt/c/`. Bench workspaces are bind-mounted into containers and cross-filesystem mounts are slow. Set `FRAPPE_MANAGER_HOME` if you need fm's directory somewhere other than `~/frappe`.
- Windows 11 resolves `*.localhost` on its own, so no hosts file edit is needed.
- Windows 10 may need an entry in `C:\Windows\System32\drivers\etc\hosts`:

```text
127.0.0.1 mybench.localhost
```

See also: [Installation](../getting-started/installation.md) for the full install options and prerequisites.
