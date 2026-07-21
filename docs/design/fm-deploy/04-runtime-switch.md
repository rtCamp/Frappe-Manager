# Prod runtime & the `switch` pipeline

> Part of the `fm deploy` design set — see [`README.md`](README.md). Section numbers (`§N`) are stable across the set.

## 7. Compose & process model (both runtimes)

Supervisor stays. fm keeps its existing model: each compose service (`frappe`/web, `socketio`, `schedule`, `worker-<queue>`) runs its **one** program under its **own** `supervisord` (via `launch_supervisor_service.sh`); worker concurrency = supervisor `numprocs = background_workers`; `multi_queue_consumption` + custom `[workers]` (bench config) decide which worker services render. This applies to **both runtimes** — `mount` benches run these services off the fm base image with code live-mounted; `image` benches off the baked app image. The `image` runtime is **additive** on this model — no all-benches supervisor-removal migration.

Each service is one `supervisord` supervising a single program (`templates/supervisor.conf.tmpl` — `frappe-web` gunicorn via `config/fm-web-server.sh`, `node-socketio`, `frappe-schedule`, workers `default`/`short`/`long` + custom queues). fm renders which worker services exist from `multi_queue_consumption` and the bench's `[workers]`; each service starts its **own** supervisor — there is no shared dispatch entrypoint.

- **`launch_supervisor_service.sh` stays**: PID 1 = `supervisord`, which manages the single program for that service (graceful stop via supervisor + `stop_grace_period`).
- **Worker concurrency**: supervisor's `numprocs = background_workers` stays — worker scaling is **not** moved to compose replicas. `multi_queue_consumption` and custom `[workers]` (bench config) decide which worker services render — reuse `bench_workers.py` logic rather than reinventing.
- **Gunicorn**: workers/timeout/`--preload` from the bench's `gunicorn_args`; observability (NewRelic / OpenTelemetry — see `fm-web-server.sh:5-30` and `BakeConfig.Observability`) preserved via env + baked agent.
- **Images**: `image` runtime pins every service to the **same app image tag**; `mount` runtime uses the fm base image + mounted code. `nginx`/assets per §9; `redis-cache`/`redis-queue` stay stock images.
- **Routing**: prod keeps fm's existing nginx-proxy integration — the `nginx` (app-nginx) service carries `VIRTUAL_HOST`/`VIRTUAL_PORT` and joins `global-frontend-network` (`docker-compose.tmpl:26-28`); the global `nginx-proxy` re-resolves the upstream as containers come and go, which is exactly what powers the §8.1 **rolling cutover** (new web stack joins → proxy balances → old drains out) without touching the proxy.
- **Volumes**: `image` mounts data only — the *site-data subpaths* (`sites/<sitename>/`, `sites/common_site_config.json`, `sites/apps.txt`) + `logs`, **not** the whole `sites/` tree (baked `sites/assets` must not be shadowed — §9), and **no** code mount. `mount` additionally bind-mounts `./workspace` for live code. Networks/services wiring to `global-db`, `redis`, `global-*-network` unchanged (`docker-compose.tmpl:104-112`).

`mount` benches run the **same** supervisor-managed services, just off the fm base image with code live-mounted; the only difference from `image` is the code source and mounts (§3).

## 8. The `switch` pipeline (local)

`fm switch <bench> <tag>` — and the second half of `fm deploy` — runs this via a new `DeployOrchestrator` (sibling of `BenchOrchestrator`), reusing fm services. `bake` produced `<tag>`; `switch` activates it:

1. **Fetch** — registry `docker pull <tag>`; or `docker load` (save_load); no-op if the tag is already local.
2. **Pre-flight** — `docker run --rm <tag>` boot check: gunicorn/frappe import + start (process health / `bench version`). Fails ⇒ **abort before any change** (nothing touched, no page), keep old tag. Catches a broken image cheaply, *before* the irreversible migrate.
3. **Backup** (`deploy.backups`) — **`bench backup` (with site config)** via exec into the **old/current** running container; the dump lands in the sites volume (host-accessible). Uniform with the exec model — no host→DB connection (§8.3).
4. **Maintenance ON — *only* if this deploy changes the schema** (`bench migrate` and/or a new-app `install-app`). No schema change ⇒ **no page** (§8.1). Writes `common_site_config` (`bench set-config` via exec).
5. **Drain + stop workers** — fm drains RQ by exec'ing in-container `fmx` (suspend + poll-until-idle, `drain_workers`/`skip_stale_workers`, §8.3), then `compose stop worker-* schedule` (SIGTERM → finish job → `stop_grace_period = worker_kill_timeout` → SIGKILL). Workers always cycle onto the new image; they're background (not behind the proxy), so this needs no page.
6. **Migrate** (`deploy.migrate`) — one-shot new-image container `docker run --rm <tag> bench migrate` (`migrate_timeout`). Transactional/resumable (`patch_log`) ⇒ **re-run to resume**; fail ⇒ **no swap**, keep old tag, report; DB restore only if `restore_on_failure` (§8.2).
7. **Rolling web swap (default, health-gated)** — bring up the **new-tag web stack** (gunicorn + assets-`nginx`) **beside** the old (`compose up -d --scale`, no fixed `container_name`); `--wait` blocks on the web healthcheck (`/api/method/ping`, §16). Healthy ⇒ nginx-proxy re-resolves to include it; **drain old** (touch `/tmp/drain` → its healthcheck fails → proxy stops routing) → `stop`+`rm` old → scale back. **Unhealthy new ⇒ old is never removed, keeps serving** → halt + report (§8.2). No-migrate ⇒ zero downtime; migrate ⇒ the swap runs behind the page.
8. **Finalize** — resume RQ; **site-DB ops only** in the new container (`install-app` for new apps, `search-replace`, `clear-cache`); **maintenance OFF** (if it was on). **Never `pip install` / `bench build` at runtime** — code, deps, assets are baked (§6, §9).
9. **Record** `<tag>` as `current`, prior as `previous` in `[deploy.state]`.

### 8.1 Rollout & zero-downtime

**Rolling/blue-green web is the default swap mechanism (both runtimes) — built into fm, not the `docker rollout` plugin.** fm implements the algorithm itself (borrowed from [`wowu/docker-rollout`](https://github.com/wowu/docker-rollout)) as step 7 of *this* pipeline: `compose up -d --scale <web>=2 --no-recreate` starts the new-tag web stack beside the old → `--wait` health-gate `/api/method/ping` (wait > healthcheck `interval × retries` + in-flight drain) → nginx-proxy re-resolves to the new container → **drain old** (touch `/tmp/drain` so its healthcheck fails → proxy stops routing) → `stop`+`rm` old → scale back → record tag in `[deploy.state]`. Running two web replicas at once requires: **(a)** dropping the web service's fixed `container_name` so two replicas coexist (a Docker constraint — two containers can't share a name — independent of supervisor); **(b)** each web replica getting its **own** `/fm-sockets/frappe.sock` (per-replica, not a shared socket) so its `supervisord`/gunicorn listens independently; **(c)** the health-gate → drain → remove sequence above. nginx-proxy balances by `VIRTUAL_HOST` (§7); supervisor is untouched — it keeps managing each replica's single program.

**Why built-in, not the plugin:** fm already owns the compose lifecycle, health-gating (`up -d --wait`), tag/state pinning, remote (`DOCKER_HOST`), and rollback (re-pin previous). The plugin does only the web-swap slice and would introduce a *second* orchestration path (its own `compose up --scale`) with no shared source of truth — the "who owns compose" split rejected in §3.1 — and knows nothing about migrate/RQ-drain/backup/rollback. It saves ~50 lines but adds a per-host dependency.

**The maintenance page is bound to the *migrate step* and nothing else.** A deploy that changes the schema (`bench migrate` and/or a new-app `install-app`) raises the page before migrate (step 4) and drops it after the new stack is healthy (step 8) — because across the migrate→swap window the **old** container would otherwise serve against the **new** schema (500s / wrong data if the migration is destructive, which fm can't detect: Frappe patches are arbitrary Python). A deploy with **no schema change** shows **no page at all** — pure rolling, true zero-downtime.

*Advanced opt-out (own the risk):* if you *know* the migration is backward-compatible/additive (expand/contract — old code tolerates the new schema), set `maintenance_mode_phases = []` (or `--additive`) to skip the page for a fully zero-downtime migrate. Default keeps the page — **fm never assumes compatibility.**

Workers/scheduler always drain gracefully then cycle onto the new image (§8.3), page or not — they're background, not behind the proxy.

### 8.2 Rollback + migration safety

Rollback is **`fm switch <bench> <previous>`** (or `fm rollback`) — re-runs pinned to the previous tag with **migrate skipped**; safe only when no schema migration ran since that tag (or migrations are backward-compatible).

**`bench migrate` is transactional and resumable** — data patches run in transactions, and applied patches are tracked in `patch_log`, so a failed migrate can simply be **re-run** (it resumes). So the **default on failure is NOT to restore the DB**. (Caveat: MariaDB DDL auto-commits, so a failure mid-schema-change can leave a half-applied `ALTER` — this is what the opt-in restore is for.)

Failure handling by phase — **default = halt + report** (no destructive restore):
- **pre-flight (step 2)** — image won't boot → abort; DB untouched, old tag kept, maintenance never entered.
- **migrate (step 6)** — no swap; keep old tag; report. **Re-run `fm deploy`** to resume the migration (`patch_log`), or fix the patch.
- **swap / health-gate (step 7)** — with rolling the **old stack is still up**, so an unhealthy new container is kept out of rotation / removed and **old keeps serving**: no-migrate ⇒ zero user impact; migrate ⇒ old stays behind the page. Halt + report; **fix-forward** (redeploy a fixed image; migrate is idempotent) or roll back (`restore_on_failure` → restore DB + re-pin previous).

**`restore_on_failure` (opt-in, default off)** additionally restores the step-3 DB backup on a failed migrate/switch and re-pins the previous tag — for when you want an automatic clean rollback instead of fix-forward. `rollback` (re-pin previous tag) and `restore_on_failure` (restore DB) are **separate**; a DB restore only ever fires when migrate ran *and* the option is on (never silently).

**DB encryption key.** Frappe encrypts some fields using `encryption_key` in `site_config.json`. A DB restore must use the key that matches the restored dump — restoring a DB without its matching key silently breaks encrypted fields. So step-3 backs up **DB + `site_config`/configs together**, and a restore puts back the matching `encryption_key` (fmd's `BackupService.sync_db_encryption_key_from_site` is the reference). `site_config.json` lives in the persistent `sites` volume (survives tag swaps); the care is only around DB restore/rollback.

### 8.3 Drain / migrate / failover — via in-container `fmx`

Today the restart/drain/migrate/maintenance failover is owned by **`fmx`**, an in-container CLI in the frappe image (`fmd` calls `fmx restart --drain-workers --migrate --maintenance-mode <phase>` via `runner.restart_services`). Its `_run_migrate_flow` (`Docker/frappe/fmx/fmx/commands/restart.py`) is the battle-tested sequence we keep — and **we keep `fmx` itself**: fm orchestrates by calling the in-container `fmx` via `exec` exactly as `fmd` does. A host-side port of `fmx`'s logic is **optional/deferred**, not required. fm owns the outer orchestration; `fmx` owns the in-container RQ drain / maintenance:

| piece | owner | runs |
|---|---|---|
| maintenance on/off | `fmx` (`maintenance_mode` in `common_site_config.json`) | `fmx` via exec into the running bench container |
| RQ drain / resume | `fmx` (suspend flag + poll-until-idle, `skip_stale`, `worker_kill_timeout`) | `fmx` via exec into the running bench container |
| migrate | `bench migrate` (invoked by `fmx`, or as a one-shot) | fm runs it in a one-shot container (`docker run --rm <tag>`) |
| process restart/kill | **compose** `stop`/`up` + `stop_grace_period` (SIGTERM warm-shutdown) | fm host code |
| compose lifecycle · rollback · `[deploy.state]` | **fm** | fm host code |

So `fm restart` (host-side) owns the outer flow: maintenance on → drain RQ → graceful `compose stop` → migrate (one-shot container) → `compose up` new tag → resume RQ → maintenance off — driving the in-container `fmx` via `exec` for the drain/maintenance pieces. On migrate failure: don't swap, keep/restart old services, resume RQ, restore DB if migrate partially applied (§8.2). `bench`/`fmx` run *in* containers (they need app+DB+redis); fm owns **compose lifecycle, the migrate one-shot, rollback, and `[deploy.state]`**.

**Decision: keep the in-container `fmx` CLI (it lives in the frappe image); fm calls it via `exec`.** fm runs drain/resume/maintenance by exec'ing `fmx` in the running container (see Connectivity below), runs `bench migrate` as a one-shot container, and drives compose for process lifecycle. A host-side port of `fmx` is **optional/deferred**. `fmx` continues to serve both image and legacy `fmd`/supervisor benches.

**Connectivity.** fm does **not** open a host→redis connection. It runs `fmx` **via exec into the running bench container** (the `fm shell` / `--bench-console` path), where the configured `redis://redis-queue:6379` resolves on the network. No published ports, `socat`, or tunnels — and it works **local and remote uniformly** via `DOCKER_HOST` (§10). (`bench migrate` runs in a one-shot `docker run <new-tag>`; process lifecycle via compose.)

**`fm restart` is the one owner.** Plain `fm restart <bench>` (no new tag) = maintenance → drain → `compose restart` → resume — **no bake, no migrate, no tag change** (for config/env changes or a plain bounce). **`switch` = that same flow + a new image tag + migrate** — `DeployOrchestrator` is the thin "restart with a new tag" wrapper. One implementation, so drain/graceful-restart/resume never diverges between a plain restart and a deploy.
