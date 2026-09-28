# Web serving and concurrency

How a request reaches your site, and how many requests a bench can handle at once.

## The request path

Every request hits the machine-wide `nginx-proxy` (ports 80/443), which routes by the `Host:` header to the right bench's own nginx, which proxies to the bench's web process. Full topology: [Architecture](../reference/architecture.md).

```
browser -> nginx-proxy (routes by domain) -> bench nginx -> web process
```

The bench's nginx does more than forward. It serves the site's own `public/` files off disk, sends `/socket.io` to the socketio container instead of the web process, and answers `/.well-known/acme-challenge/` itself so certificate renewal works without touching Frappe.

`/assets` is the interesting one, because where nginx looks depends on the [runtime](runtimes.md). Its document root is `/workspace/frappe-bench/sites`: on a **mount** bench that is the bind-mounted workspace, so nginx serves whatever `bench build` or `bench watch` last wrote to disk; on an **image** bench the container runs the release's paired `-nginx` image with the bundles baked in, and the image-mode binds are data-only, deliberately not covering `sites/assets`.

A miss falls through to the web process, and that rescues a `dev` bench only: `bench serve` wraps the app in Frappe's static middleware and serves `/assets` itself, which is what covers the window after a `bench watch` rebuild emits content-hashed filenames nginx has not seen. Gunicorn imports the bare WSGI app with no static middleware, so on a `prod` bench a missing bundle is a 404 that nginx cannot cover for. Rebuild it (`fm shell mybench -c "bench build"`) on a mount bench; rebake on an image bench.

## The web process: dev server vs Gunicorn

Which web process runs is decided by the bench's [environment](environments.md):

| | `dev` | `prod` |
|---|---|---|
| Process | `bench serve` (Werkzeug) + `bench watch` | Gunicorn, via the generated `config/fm-web-server.sh` |
| Concurrency | one process, a thread per request, no worker pool | workers x threads |
| Hot reload | yes (Werkzeug's reloader for Python, `bench watch` for assets) | no |
| On crash | stays down: supervisor does not restart the dev server | restarted by supervisor |

Switching is a settings change: `fm update mybench --environment prod` (see [Environments](environments.md)).

## Gunicorn workers and threads

fm sizes Gunicorn automatically:

```
workers = min(CPU count, RAM in MB / 256)
```

One worker per CPU core, capped so each worker has roughly 256 MB of RAM available, and never below 1. Workers use the `gthread` class, so each worker additionally serves several concurrent requests on threads.

Every value is read from `common_site_config.json` when present, otherwise computed:

| Key | Default | What it sets |
|---|---|---|
| `gunicorn_workers` | `min(CPU count, RAM MB / 256)` | `-w`, the worker process count |
| `gunicorn_threads` | `max(2, min(CPU count, 4))` | `--threads`, threads per worker |
| `gunicorn_max_requests` | `1000` | `--max-requests`, after which a worker is recycled (jitter is 10% of it) |
| `http_timeout` | `120` | `-t`, the seconds a request may take before Gunicorn kills the worker |

!!! warning "`http_timeout` above 120 does nothing on its own"
    The bench nginx proxies with a hardcoded `proxy_read_timeout 120`, so a request that outlives 120 seconds is cut off at that hop whatever Gunicorn is willing to wait. Long work belongs in a [background job](background-jobs.md), not a long request.

**Examples:**

- 4-core machine, 8 GB RAM -> 4 workers (CPU-bound)
- 8-core machine, 1 GB RAM -> 4 workers (RAM-bound: 1024 MB / 256)
- 2-core machine, 512 MB RAM -> 2 workers

### Overriding the worker count

The Gunicorn command line lives in a generated script, `config/fm-web-server.sh`, so a new value needs a regeneration pass before the restart picks it up:

```bash
fm shell mybench -c "bench set-config -g gunicorn_workers 4"
fm start mybench --reconfigure-supervisor   # rewrite config/fm-web-server.sh
fm restart mybench --web --no-workers       # re-exec it
```

!!! warning "Too few = slow, too many = OOM"
    Too few workers and requests queue up; too many and RAM runs out. The default formula balances CPU utilization and memory; override only with a measured reason.

## HTTPS and the client's address

TLS terminates at the global proxy, so the bench's nginx and Frappe both speak plain HTTP. Two headers carry what was lost, and both hops are configured to pass them:

- **`X-Forwarded-Proto`.** The global proxy sets it to the scheme it terminated, and passes an inbound value through untouched when one is already present (so a CDN's value wins). The bench nginx forwards that same value verbatim. Frappe reads the header directly, not the WSGI scheme: `frappe.utils.get_url()` treats exactly the value `https` as HTTPS and anything else as HTTP, and that decides every absolute URL it builds (email links, redirects, OAuth callbacks). Setting `host_name` in the site config overrides all of it, which is why `fm ssl add` writes it only when the certified domain is the site's own canonical name, and leaves it alone for an alias.
- **`X-Real-IP`.** The bench nginx only ever sees the global proxy's address, so fm writes a `real-ip.conf` overlay that trusts the fm frontend network and takes the client from `X-Real-IP`, which the proxy sets from its own `remote_addr`. fm re-materialises that overlay on every `fm start`, since nginx reads its includes once, at boot. Behind a CDN the proxy's own `remote_addr` is the edge, not the visitor: `fm services real-ip` is what teaches it otherwise.

## Customizing the bench's nginx

fm owns exactly one file in a bench's nginx config: `configs/nginx/conf/conf.d/default.conf`. It is **generated output**, rendered from the bench's recorded sites every time the nginx container boots, so anything you write into it is lost the next time the container starts. Everything else in that directory is yours and is never touched.

| You want | Write | Applies to |
| --- | --- | --- |
| Behaviour for every site in the bench | `configs/nginx/conf/custom/<name>.conf` | inside every site's `server` block |
| Behaviour for one site | `configs/nginx/conf/custom/<site>/<name>.conf` | inside that site's `server` block only |
| Your own `server`, `map`, or `upstream` | `configs/nginx/conf/conf.d/<name>.conf` | the `http` block |
| To take over one domain completely | `configs/nginx/conf/conf.d/<domain>.server.conf` | that domain, instead of fm's block |

The first two are included from inside a `server` block, so they take directives like `client_max_body_size`, `add_header` or a `location`, and a `server { ... }` there is a syntax error. The third is included at `http` level and takes whole server blocks.

The last row is the escape hatch. Naming a file after a domain **claims** it: fm leaves that domain out of its own render entirely, no map entry and no server block, so your file is the only thing serving it. Every other domain in the bench keeps being regenerated normally. `fm info` lists claimed domains, because a claimed domain no longer follows the bench's own settings and that is easy to forget months later.

Changes take effect when nginx re-reads its config. `fm restart --service nginx` is enough for a drop-in; a new or removed domain needs `fm restart` so the container is recreated with the new routing.

If a render would produce a config nginx cannot load, fm keeps the previous one and says so on the container's logs rather than starting with broken routing. If the invalid file turns out to be one of yours, the message says that instead, so `fm logs` on a bench whose nginx will not start is the first place to look.

## See also

- [Background jobs and workers](background-jobs.md): the other kind of "worker"
- [Environments](environments.md): switching dev/prod
- [Architecture](../reference/architecture.md): containers and networking
