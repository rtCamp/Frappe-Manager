# Data layout & Remote deploy

> Part of the `fm deploy` design set — see [`README.md`](README.md). Section numbers (`§N`) are stable across the set.

## 9. Data layout & state (prod)

```
~/frappe/sites/<bench>/
├── docker-compose.yml        # fm-generated, prod variant, image-pinned
├── workspace/
│   └── <bench>/sites/        # DB config, files, site_config.json  (persistent volume)
├── configs/                  # nginx, logs (as today)
├── backups/                  # pre-deploy backups
└── bench_config.toml         # + [deploy], current/previous tag under [deploy.state]
```

No `frappe-bench` symlink / per-release trees in prod — the image *is* the code. Persistent data keeps its current contract so backup/search-replace work unchanged.

**First deploy / bootstrap (decided: `fm create` provisions, `fm deploy` ships).** A fresh image bench has no `sites` volume or `common_site_config.json`. `fm create <bench> --environment prod` (which defaults `runtime=image`) — or `fm create <bench> --runtime image` explicitly — owns provisioning: sites dir, DB, SSL, networks, `common_site_config.json` (db/redis wiring), reusing the existing `bench_site.py`/`bench_config` generation, but **branches on `runtime` to skip building/running an app** (no image exists yet), and records `[deploy]`. The first `fm deploy` then bakes + runs the image; if the bench isn't created, `fm deploy` errors with a hint to run `fm create` first. Later deploys only swap the image tag — the sites volume persists across tags, and `common_site_config.json` lives in the volume (not the image) so infra endpoints change without a rebuild. This keeps `fm deploy` single-purpose (build/ship/switch) and avoids duplicating create's provisioning.

**Assets (decided): bake into the image, serve via the `frappe-app-nginx` image (§6).** `bench build` output ships and versions atomically with its code, so a rollback to a previous tag restores matching assets automatically and the `sites` volume stays pure data. **Implementation note:** mount the *site-data subpaths* (`sites/<sitename>/`, `sites/common_site_config.json`, `sites/apps.txt`) as volumes rather than the whole `sites/` tree — otherwise the data volume shadows the image's baked `sites/assets`. The prod `nginx` service runs the `frappe-app-nginx` image pinned to the same tag.

## 10. Remote deploy (ship-style)

`fm deploy <bench> --remote` (requires `[deploy.remote]`) chains **bake → transport → switch on the target**. Because fm drives Docker as a plain client (§3.1), "switch on the target" has two mechanisms:

- **(preferred) `DOCKER_HOST=ssh://<remote>`** — the local `DeployOrchestrator` drives the *remote* daemon directly: compose up/stop and `docker run`/`exec` (migrate, bench ops, fm's redis drain) all execute remotely over the SSH-tunneled Docker socket. **No `fm` install on the remote → no version skew** (§13). fm still needs SSH file access to read/write the remote `bench_config.toml` and data (rsync, as fmd does).
- **`ssh remote fm switch`** — run fm on the remote (mirrors fmd's `ShipManager`): rsync `bench_config.toml`, ensure a compatible `fm`, invoke `fm switch`. Fallback when driving the remote daemon over SSH is undesirable (latency, large transfers).

Transport, driven by `registry.distribution`:
- **registry**: `fm bake --push` → target pulls `<tag>` (via the remote daemon or remote fm).
- **save_load**: `fm bake` → `docker save <tag> | ssh 'docker load'` (no registry).

**Registry auth:** `docker login <registry>` from `[deploy.registry]` creds (env-substituted, `--password-stdin`) runs before push/pull against the active daemon — including the remote daemon under `DOCKER_HOST=ssh://`; ambient daemon creds are used when none are configured.

`bake` (provision `docker run` + `COPY`, §6) runs locally / in CI, or on the target via `DOCKER_HOST=ssh://<remote>` to offload it. Cross-arch needs provisioning under the target arch (emulation); save_load is single-arch. Either way the **same `switch` pipeline** runs, so drain/migrate/rollback behave identically to local.
