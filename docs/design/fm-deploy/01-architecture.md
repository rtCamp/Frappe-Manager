# Architecture

> Part of the `fm deploy` design set — see [`README.md`](README.md). Section numbers (`§N`) are stable across the set.

## 1. Why `fm`, not `fmd`

Image-based deployment is an **orchestration** concern, and `fm` already owns orchestration:

- fm generates the bench `docker-compose.yml` from `templates/docker-compose.tmpl` (services: `frappe`, `nginx`, `socketio`, `schedule`, `redis-cache`, `redis-queue`).
- fm owns the base images (`ghcr.io/rtcamp/frappe-manager-frappe`, `-nginx`), the shared services (`global-db`, `nginx-proxy`), networking, SSL, `code`, and the migration system.
- fm **already models dev/prod**: `FMBenchEnvType {prod, dev}` and `BenchConfig.environment_type` (`site_manager/bench_config.py:325,921`), already branching behavior (restart policy at `:980`). #323's `--environment` maps onto this existing enum.
- `fmd` currently reaches *into* fm to orchestrate (imports `ComposeFile`, `ComposeProject`, `BenchSupervisor`, `MariaDBManager`, `MigrationServicesManager`). That inversion is the signal the logic belongs in fm.

Consequence: **the "compose ownership" risk** from the fmd proposal (§12 there) disappears — fm owns the compose end to end; no overlay/clobber conflict.

## 2. Goals / Non-goals

**Goals**
- `fm deploy <bench>`: build/pull an immutable app image, run the prod bench from it (gunicorn + workers + scheduler), data-only mounts.
- **Add the `image` runtime with rolling zero-downtime web swaps** (#323): the `image` mode is additive on fm's existing supervisor-per-service model — supervisor is **retained** in both runtimes.
- Local-host deploy and **remote deploy** (build here → transport → switch on remote over SSH).
- Full switch pipeline: backup → maintenance mode → migrate → search-replace → rollback (re-pin previous image tag).
- Distribution via registry push/pull and `docker save`/`load` over SSH.
- Keep `dev` on the live-mounted iterate flow (editable code, `bench serve`) — retaining fm's existing per-service supervisor model (no migration).
- Share deploy primitives so `fmd` can consume them instead of duplicating.

**Non-goals (this iteration)**
- k8s/swarm / multi-node scheduling. Single-host compose per bench.
- Removing `fmd`'s release-based flow.
- Changing FM's dev-bench provisioning defaults.
- Multi-site per bench: fm is single-site (bench = one site, `_phase4_create_site`); the deploy pipeline scopes to that site (Decision 7).

### 2.1 Deliberate divergence from frappe_docker

`frappe_docker` is the canonical image path and validates the core patterns we adopt (bake apps + assets, tag-swap + `bench migrate`). But fm **deliberately does not align with or reuse it**: fm keeps its supervisor-per-service model and drives Docker as a **plain client** — managing the bench's containers as *siblings* on whatever daemon it targets (host socket locally, `DOCKER_HOST=ssh://` remotely), orchestrating the whole lifecycle itself — rather than shipping as an in-cluster `configurator` + compose/k8s manifest set. That gives fm what frappe_docker can't:

- **Host-side orchestration** of the whole switch — backup, drain/failover (via in-container `fmx`, called by `exec`), migrate, tag-swap, rollback — as one CLI flow (§8), not a manual "edit tags → down → up → migrate" runbook.
- **uv/fnm runtime parity** between dev-release and prod-image (§6): one runtime model, exact-version reproducibility; frappe_docker's Containerfile does neither.
- **Integrated drain/migrate/backup, SSL, networking, global services, single-tool UX** — deploy lives in the same tool that already owns the bench.
- **`fm create` provisioning** replaces frappe_docker's `configurator` init-container (Decision 4).

We take *inspiration* from frappe_docker's validated patterns but **own the build (§6), compose (§7), and pipeline (§8)**. Reusing its Containerfile/compose was considered and rejected: it would force fm into an in-cluster model, drop uv/fnm parity, and couple fm to an external project's structure.

## 3. Architecture: two axes (supervisor stays)

Two **independent** axes:

- **`environment_type` (`dev` / `prod`) — the settings bundle (unchanged).** developer_mode, admin_tools, restart-policy default, dev packages, `frappe_server_mode` default, `FRAPPE_ENV`. What fm's env flag already means; we do **not** overload it.
- **`deployment_mode` (`mount` / `image`) — the runtime (new).** The only difference is *where code comes from*.
- **Supervisor is retained** (both runtimes). fm keeps its existing per-service `supervisord` model; the `image` mode is **additive** on it, not a migration for existing benches.

| | `mount` (dev / local) | `image` (deploy) |
|---|---|---|
| Container image | fm base image + live-mounted `./workspace` code | immutable app image (apps baked) |
| Code edits | in place; `fm update` mutates the workspace | none; `fm deploy` = new tag, rollback by tag |
| Mounts | workspace (code) + persistent data | persistent data only (`sites`, `logs`, `config`) |
| `[deploy]` config | ignored | required |

**Shared by both:** the supervisor-per-service model (`frappe`/web, `socketio`, `schedule`, `worker-<queue>`, each running its one program under `supervisord` via `launch_supervisor_service.sh`; workers scale via `numprocs=background_workers`); `frappe_server_mode` (bench serve / gunicorn) and `environment_type` settings apply to both; the switch/drain/restart orchestration (§8, §8.3) is one code path over compose.

`deployment_mode` defaults to `mount`. The axes are orthogonal: `mount`+`dev` (local dev), `mount`+`prod` (prod on mounted code, e.g. build-on-server), `image`+`prod` (the immutable target), `image`+`dev` (test the image runtime locally). Per #323, `--environment prod` on a **new** bench *defaults* `deployment_mode=image`, but the two are set and stored independently.

`frappe_server_mode: {bench_serve, gunicorn}` stays its own field (defaulted from `environment_type` — dev=serve, prod=gunicorn — independently overridable per #323: "prod server in dev workflow").

### 3.1 Execution model

fm drives Docker as a **plain client**: no daemon nested in a container (not DinD) and no host socket mounted into a container — the containers fm manages are **siblings** on whatever daemon it targets. Every deploy action is one of two host-issued primitives (generalizing fmd's runner — `image` = `docker run`, `exec` = `docker compose exec`):

- **Container lifecycle** → `docker compose` (up / stop / rm) against the bench's generated compose file.
- **In-container ops** → `docker compose exec <svc> …` (running service) or `docker run --rm <tag> …` (one-shot, e.g. migrate) — this is how `bench migrate`, `bench install-app`, search-replace, and clear-cache run in the bench. (Maintenance toggle and RQ drain are **fm host code**, not in-container — §8.3.)

The daemon target is a variable, not the code path: local uses the host Docker socket; **remote uses `DOCKER_HOST=ssh://<remote>`**, so the *same* `DeployOrchestrator` drives a remote daemon unchanged (§10). In CI it's the same — fm just points at whatever daemon is available (the runner's, or the remote over SSH); no socket-sharing tricks needed. `bench` is in the app image, so in-container ops (`exec`/`run`) work wherever the image runs; the drain/maintenance orchestration itself is fm host code (§8.3). This is why deploy/switch/drain/rollback behave identically local and remote — only the daemon endpoint moves. `bake` is daemon-targeted too (provision `docker run` + `COPY`-into-image, §6), so it runs locally, in CI, or on the target via `DOCKER_HOST=ssh://` (§6, §10).

### 3.2 Impact on existing commands (image-runtime branch)

`deployment_mode=image` is a branch point across the command surface, not just `deploy`. Both runtimes share the supervisor-per-service compose model; the branch is about **immutability** — `mount` benches keep mutable-code behavior (edit in place, `fm update`), while `image` benches re-point commands at immutable-image semantics (deploy by tag, `[deploy.state]`, ephemeral shell, no `/workspace` code mount).

| Command | `image`-runtime behavior |
|---|---|
| `update` | **Loses code updates.** App/version/code changes are immutable in `image` mode → done via `bake` + `switch` (`deploy`), **not** `update`. Here `fm update` handles only non-code concerns: SSL, admin-tools, env, restart policy, infra image tags (nginx-proxy/global), config regen. It must **refuse app/version edits in image mode** and point to `fm deploy`. |
| `start` / `stop` | `compose up -d` / `compose stop` on the image service set (web/workers/schedule/socketio/nginx) at the `[deploy.state].current` tag. |
| `restart` | **The host-side owner of the drain/graceful-restart orchestration** (§8.3): sequences maintenance → **drain via in-container `fmx` (called by `exec`)** → `compose restart`/recreate → resume — **compose for both runtimes**. `switch` is literally this restart **+ a new image tag + migrate**. The `--supervisor` flag remains; `--web`/`--workers` select service groups. |
| `logs` | `docker compose logs <service>` (per-container stdout/stderr) for both runtimes; per-service supervisor logs remain available inside each container. |
| `shell` | `compose exec <svc> bash` (default `frappe`). In image mode **ephemeral** — edits don't persist (no workspace mount) and are lost on the next `switch`; warn. |
| `code` | **Mount**: live-mounted editable debug. **Image**: **reproduce + observe** (§15) — throwaway `frappe-debug` from the current tag against live db/redis (VSCode attaches *into* the container; baked source, no `pathMappings`) for reproducible bugs; logs / traces / `py-spy` for live-only issues. Can't breakpoint the live worker without pausing it. |
| `info` | Show `deployment_mode` + `environment_type`, current/previous tag, running services + health. |
| `list` | Annotate benches with runtime (mount/image) + env + current tag. |
| `reset` | Recreate the site in the data volume; image untouched (no code reset). |
| `delete` | `compose down`; remove data + optionally prune the bench's image tags (§9). |
| `migrate` (fm's version-migration system) | Applies to both runtimes; migrations must not assume the workspace layout when the bench is image-mode (the supervisor layout is preserved in all modes). |
| `ngrok`, `ssl`, `services`, `self` | Unaffected — operate on proxy / certs / global services / fm itself. |

**The key boundary — `update` vs `deploy` (image mode):** today `update` mutates the live workspace/config in place; in `image` mode that's impossible (immutable image), so responsibilities split cleanly — **`deploy` = code / apps / deps (rebuild image + switch)**, **`update` = everything else about the bench (SSL, infra, env, policy)**. Make it explicit or users will reach for `update` expecting code changes.
