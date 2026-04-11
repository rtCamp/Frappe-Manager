# Logs & Debugging

Where logs live and how to view them.

- CLI log: `~/frappe/logs/fm.log`
- Bench logs (web/dev server): `~/frappe/sites/<bench>/workspace/frappe-bench/logs/`
- Service logs (global): `~/frappe/services/<service>/logs`

Tail logs with the CLI:

```bash
fm logs mybench --service frappe -f
```

CLI logs rotate automatically. If you need to inspect older logs, look for `fm.log.1`, `fm.log.2`, etc.

You can make fm more talkative:

```bash
fm --verbose info mybench
fm --log-level debug info mybench
```

Common service names for `--service`: `frappe`, `nginx`, `socketio`, `schedule`, `redis-cache`, `redis-queue`, `short-worker`, `long-worker`.
