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

## Restarting workers safely

`fm restart` kills worker processes immediately. Any in-flight job is interrupted.

For production — or any time you are running an email send, file import, or report that takes minutes — drain the queues first using [`fmx`](../guides/fmx.md):

```bash
# Wait for all in-flight jobs to finish, then restart
fm shell mybench -c "fmx restart --drain-workers"
```

To also run a database migration as part of the same step:

```bash
fm shell mybench -c "fmx restart --drain-workers --migrate"
```

See the [fmx guide](../guides/fmx.md) for the full list of restart options including maintenance mode and per-service restarts.

## Pausing workers without restarting

To temporarily stop workers from picking up new jobs — for example while you apply a manual database patch — without restarting anything:

```bash
fm shell mybench -c "fmx rq suspend"
# ... do your work ...
fm shell mybench -c "fmx rq resume"
```
