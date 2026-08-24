# Worker care: restarting without losing jobs

`fmx` drives one container's supervisor programs: `frappe` (Gunicorn), `short-worker`, `long-worker`, `schedule`, `socketio`. Cycling the web server and the scheduler is uninteresting; cycling the RQ workers is not, because each one may be holding a job. This note is about that.

The host-side view, including how `fm restart` and `fmx restart` divide the work and the `[workers]` config keys that drive the host, lives in `docs/guides/fmx.md` and `docs/reference/configuration.md#workers`. Everything below is the in-container surface.

## Check what is running first

```bash
fmx status --verbose     # supervisor state, suspend flag, queue depths, current job per worker
fmx rq status --verbose  # the RQ view on its own
bench --site your.site.name doctor
```

Frappe's **RQ Job** list in the desk UI shows the same queues if you would rather look at them there.

## The drain

`fmx restart` drains by default:

1. Set RQ's suspend flag in Redis, so no worker picks up another job.
2. Poll (`--drain-workers-poll`, default 5 seconds) until every worker is suspended, for at most `--drain-workers-timeout` seconds (default 300; `0` waits indefinitely).
3. Restart the targeted services.
4. Clear the suspend flag. This happens on every exit path, including a failure part-way through.

If step 2 runs out of time, fmx clears the suspend flag and exits non-zero **without restarting anything**: a job that outlasts the deadline is never interrupted on your behalf. Raise `--drain-workers-timeout`, or decide the job is expendable and pass `--no-drain-workers`.

A worker idle for longer than `--skip-stale-timeout` seconds (default 15) is treated as dead and no longer waited on, so a crashed worker cannot stall the drain forever. `--no-skip-stale-workers` turns that off and waits for it anyway.

`fmx stop --drain-workers` runs the same drain before stopping, with two differences: for `stop` the drain is opt-in, and its `--drain-workers-timeout` defaults to `0`, so the wait is unbounded unless you bound it.

## Skipping the drain

`--no-drain-workers` sends each worker SIGUSR1 (RQ's warm shutdown) and stops any worker still alive `--worker-kill-timeout` seconds later (default 15, polled every `--worker-kill-poll` seconds, default 3) through supervisor. In-flight jobs may be interrupted and land in the failed-job registry.

This is the development path, and the recovery path when a wedged bench matters more than a job. Those two flags only apply here: a drained restart stops workers through supervisor and never signals them.

## Migrations

`fmx restart --migrate` runs `bench migrate` between the drain and the restart. Non-worker services stay up during the migration, so the site keeps serving; if the migration fails the flow aborts and starts back whatever it had stopped.

- `--migrate-command 'bench --site mysite.localhost migrate'` replaces the default `bench migrate`.
- `--migrate-timeout 600` bounds the migration; the default `0` lets it run as long as it needs.

Keep the drain on for a migration. `fmx restart --migrate --no-drain-workers` leaves old workers executing old code against the new schema, and fmx warns as much before proceeding.

To serve Frappe's maintenance page instead of a half-working site:

```bash
fmx restart --migrate --maintenance-mode drain --maintenance-mode migrate
```

`--maintenance-mode` is repeatable and accepts `drain` and `migrate`. Each value sets `maintenance_mode` in `common_site_config.json` for that phase only, and it is cleared on every exit path. `drain` without a drain, or `migrate` without `--migrate`, warns and is ignored.

## Manual maintenance windows

`fmx rq suspend` and `fmx rq resume` toggle the flag without touching any process, which is what you want before a hand-written SQL migration. The flag is persistent: a bench left suspended looks healthy and silently processes no background jobs, so pair every `suspend` with a `resume` and confirm through `fmx rq status`.

## When it goes wrong

**A worker never drains.** `fmx status --verbose` names the job it is holding. Either raise `--drain-workers-timeout` or interrupt it with `--no-drain-workers`.

**Suspend or resume fails.** The flag lives in the queue Redis, which fmx reads from `redis_queue` in `common_site_config.json`. Check that key and that Redis is reachable.

**Services do not come back.** Supervisor writes to `/workspace/frappe-bench/logs/` inside the container; from the host, `fm logs BENCHNAME --service frappe -f` reads the container log directly.
