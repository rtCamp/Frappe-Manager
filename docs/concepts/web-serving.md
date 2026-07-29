# Web Serving & Concurrency

How a request reaches your site, and how many requests a bench can handle at once.

## The request path

Every request hits the machine-wide `nginx-proxy` (ports 80/443), which routes by the `Host:` header to the right bench's own nginx, which proxies to the bench's web process. Full topology: [Architecture](../reference/architecture.md).

```
browser -> nginx-proxy (routes by domain) -> bench nginx -> web process
```

## The web process: dev server vs Gunicorn

Which web process runs is decided by the bench's [environment](../guides/environments.md):

| | `dev` | `prod` |
|---|---|---|
| Process | `bench serve` (Werkzeug dev server) + `bench watch` | Gunicorn |
| Concurrency | single-threaded | multiple workers x threads |
| Hot reload | yes (Python + assets) | no |

Switching is a settings change: `fm update mybench --environment prod` (see [Environments](../guides/environments.md)).

## Gunicorn workers and threads

fm sizes Gunicorn automatically:

```
workers = min(CPU count, RAM in MB / 256)
```

One worker per CPU core, capped so each worker has roughly 256 MB of RAM available. Workers use the `gthread` class, so each worker additionally serves multiple concurrent requests via threads (default threads: `max(2, min(CPU count, 4))`, overridable with `gunicorn_threads` in `common_site_config.json`).

**Examples:**

- 4-core machine, 8 GB RAM -> 4 workers (CPU-bound)
- 8-core machine, 1 GB RAM -> 4 workers (RAM-bound: 1024 MB / 256)
- 2-core machine, 512 MB RAM -> 2 workers

### Overriding the worker count

```bash
fm shell mybench -c "bench set-config -g gunicorn_workers 4"
fm restart mybench
```

!!! warning "Too few = slow, too many = OOM"
    Too few workers and requests queue up; too many and RAM runs out. The default formula balances CPU utilization and memory; override only with a measured reason.

## See also

- [Background Jobs & Workers](background-jobs.md): the other kind of "worker"
- [Environments](../guides/environments.md): switching dev/prod
- [Architecture](../reference/architecture.md): containers and networking
