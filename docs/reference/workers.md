# Workers & Background Jobs

Frappe background jobs run in dedicated worker containers. The common worker types are:

- `short-worker` — for quick tasks (under a few minutes).
- `long-worker` — for long-running tasks.
- `schedule` — runs the Frappe scheduler.

Apps can define custom worker queues in their `hooks.py`. FM will create worker containers as needed.

Common operations:

- Restart workers:

```bash
fm restart mybench --workers
```

- View a worker's logs:

```bash
fm logs mybench --service short-worker
```

!!! warning
    Restarting workers while jobs are running may interrupt work. For production migrations prefer to wait for running jobs to finish.
