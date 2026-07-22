# Tasks, debug workflow & appendix

> Part of the `fm deploy` design set — see [`README.md`](README.md). Section numbers (`§N`) are stable across the set.

## 14. Task breakdown

1. Config: `runtime` field (Axis B, default `mount`) + models (`DeployConfig`, `DeployBuildConfig`, `RegistryConfig`, `RemoteConfig`) + `frappe_server_mode`; masking; TOML round-trip in `BenchConfig`.
2. Rolling enablement: in the prod compose, drop the web service's fixed `container_name` and give each web replica its **own** per-replica `/fm-sockets/frappe.sock` supervisord socket (not shared) so the web service can scale to 2 replicas — needed only for rolling. Build recipes live in `Docker/`: `Docker/frappe/runtime.Dockerfile` (COPY-only) and an `app-assets` target appended to the existing `Docker/nginx/Dockerfile` (no new nginx file). `templates/` gains only `docker-compose.prod.tmpl`.
3. Bake: extract `BenchOrchestrator`'s provisioning phase into a `provision(workspace, apps, use_run, hooks)` unit → `bake` calls it with `use_run=True` into a build-context dir (same `BenchApp.install_apps`/`setup_python_and_node_environments` as dev `create`) → `Docker/frappe/runtime.Dockerfile` `COPY`s the tree (`docker build`); tag scheme + tag pruning.
4. `DeployOrchestrator`: local switch/rollback pipeline (pre-flight → backup → maintenance/drain → migrate → `compose up -d --wait` health-gate → finalize → record) with the three failure branches (§8, §8.2); reuses `BenchService`/`MariaDBManager`.
5. Commands: `fm bake`, `fm switch`, `fm rollback`, `fm deploy` (+ `--remote`/`--image`); register + migration skip-set entries.
6. Transport: registry push/pull + `docker save`/`load`-over-SSH; `--remote` ship path reusing SSH/rsync.
7. fmd: leave as-is (legacy); track deprecation separately once fm's image path is proven — no shared-import work.
8. Docs + CLI regen; tests (config validation, tag scheme, prod compose render, rollback, remote dry-run).
9. Reuse the in-container fmx via `exec` for drain/resume/maintenance/migrate orchestration (`fmx restart --drain-workers --migrate --maintenance-mode <phase>`, as `fmd` does); host-side port of fmx deferred.
10. `fm code` (#323 item 4): keep fm's existing **launch**-based dev-container debug — VSCode `launch.json` starts `bench serve --noreload` under debugpy, with `preLaunchTask = fmx stop frappe` freeing port 80 (supervisor keeps the container alive; ↻ relaunches the bench-serve child in place). **image** = reproduce + observe (§15): throwaway `frappe-debug` from the current tag against an **isolated data copy** (restored/scrubbed), out of rotation, web-only (opt-in live db); logs / traces / `py-spy`; opt-in live attach (recoverable via `fm restart`).
11. Config layering: `--config` (repo base) + `--config-overrides` + flags + env-substitution, with precedence (§4.1).
12. Composite GitHub Action wrapping `fm bake`/`fm deploy` at fmd-action parity (§4.1).
13. Branch existing commands (`update`/`start`/`stop`/`restart`/`logs`/`shell`/`code`/`info`/`list`/`reset`/`delete`) on `runtime` (mount vs image); enforce the `update`↔`deploy` boundary (§3.2).
14. App sourcing + hooks fidelity (§6.1): clone options (branch/commit, shallow, subdir, monorepo symlink-reuse, remove_remote, token to provisioning `docker run`); build hooks run in the provisioning `docker run`; host build hooks interleave (preserved — no isolated build); switch hooks → container exec / host; hook env parity (`get_script_env`).

## 15. `fm code` — debug workflow (#323 item 4)

`fm code <bench>` opens VSCode against a bench for development/debugging (`commands/code.py`).

**Model (decided) — debugging is just *request + inspect*.** Everything else is plumbing to give you a **requestable server you can stop on**:
- **Server** = `bench serve --noreload` under **debugpy**, which VSCode **launches as a child**; `preLaunchTask = fmx stop frappe` frees port 80 while **supervisor keeps the container alive**.
- **Request** = hit that container's forwarded port (curl / browser / a replayed request).
- **Inspect** = breakpoint → variables / step / debug console.
- **Restart** = the debugger's **↻** relaunches the child; supervisor keeps the container warm (no recreate, no re-attach).

Same model both runtimes — they differ only in **data**: `mount` = live workspace + dev DB (one instance, no duplication); `image` = baked source + an **isolated data copy** (§ below). Details:


**`mount` (dev).** fm **already** does the right thing: `fm code --debugger` opens VSCode **inside the container** (dev container, `devcontainer.metadata` + `templates/vscode/`) and its `launch.json` is a **`launch`** config — VSCode itself starts `bench serve --port 80 --noreload --nothreading` under `debugpy` (via `bench_helper.py`), with a `preLaunchTask` freeing the port. So VSCode **owns the server process** and its **Restart (↻) relaunches it in place** — breakpoints/watch/console persist, no re-attach. Loops per task (best → fallback):
- **Fast inner loop (most work):** `bench serve` **hot-reload** — edit → save → refresh. No debugger.
- **Debug loop (breakpoints):** the `launch` config above — F5, ↻ to restart the server **without recreating the container** (`preLaunchTask fmx stop frappe` frees port 80; supervisor keeps the container up). `--noreload` because the reloader and a debugger conflict.
- **REPL loop (logic/data):** `bench console` / `fm shell --bench-console` / `bench execute path.to.fn` — poke functions + data directly, often faster than a request→breakpoint cycle. (fm's `launch.json` already ships "Debug specific queue"/"Debug specific function" configs.)

**Restart the server without restarting the container.** Debugging needs only two things: a **server you can send a request to** and **debugpy to stop + inspect**. VSCode **launches** `bench serve --noreload` under debugpy as a child; its `preLaunchTask = fmx stop frappe` frees port 80, and **supervisor keeps the container alive** (it does not respawn the stopped web program while the debug server holds the port). The debugger's **↻** relaunches that bench-serve child in place — breakpoints/watch/console persist, no re-attach, no container recreate.

**`image` (prod) — reproduce + observe, plus opt-in live attach.** Breakpointing a **live serving** worker pauses *its* in-flight requests — **recoverable** (`fm restart` that service restores it), but disruptive to those requests. On a **separate debug instance** or an **out-of-rotation replica** it's free. Tiers:

- **Reproduce (default, zero traffic + zero prod-data risk):** `fm code <bench>` spins a throwaway **`frappe-debug` instance from the current tag** against an **isolated data copy** (restored/scrubbed backup or a snapshot/clone) — identical baked code + realistic data, **out of nginx-proxy rotation**, **web-only** (no workers on the shared queue). Send the failing request/job to *it* and breakpoint freely — writes can't touch prod, no lock contention with live. VSCode attaches **into the container** → baked source, no local checkout / `pathMappings`. `fm restart` the debug service to bounce; ephemeral. *Opt-in:* point it at the **live** db/redis only when you need current state — then read-mostly (a paused breakpoint can hold a live lock; writes hit prod data).
- **Live attach (opt-in — you accept the interruption):** attach to a real serving worker; a breakpoint pauses *its* requests → when done, **`fm restart`** that service to return to normal. Best on a replica pulled **out of nginx-proxy rotation** (>1 web replica) so no live traffic is affected; single-web means a brief interruption + restart.
- **Observe (non-intrusive):** logs, Sentry / OpenTelemetry traces, `py-spy` (stack sampling, no pause) — for live-only heisenbugs without touching execution.

`debugpy`/`py-spy` are dev tools (installed at debug time), never baked into the serving image.

## 16. Appendix — concrete sketches (illustrative, not final)

**A. Drain/migrate/maintenance — in-container fmx via `exec`.** fm calls the existing `fmx` inside the running bench container (as `fmd` does): `fmx restart --drain-workers --migrate --maintenance-mode <phase>`. Source stays at `Docker/frappe/fmx/fmx/rq_controller.py` (suspend flag, poll-until-idle, skip-stale) + the phase logic in `commands/restart.py` (`set_maintenance_mode`, `_run_migration`, `_handle_migrate_failure`).

- **maintenance on/off** → fmx writes `maintenance_mode` in `common_site_config.json`.
- **RQ drain/resume** → fmx's `rq_controller` inside the container (`redis://redis-queue:6379` resolves on the network) — works local & remote via `DOCKER_HOST`.
- **migrate** → `bench migrate` (fmx-driven).
- **process lifecycle** → supervisor per service (`stop_grace_period` for graceful stop).

`fm restart`/`DeployOrchestrator` invoke fmx via `exec` and sequence these phases; a host-side port of fmx is optional/deferred.

**B. `docker-compose.prod.tmpl` (web + worker sketch).**

```yaml
services:
  frappe:                          # web — runs its one program under its own supervisord
    image: {{ app_image }}:{{ app_tag }}
    # no fixed container_name → the web service can scale to 2 replicas (rolling)
    environment:
      SERVICE_NAME: frappe
      FRAPPE_SERVER_MODE: gunicorn
    command: launch_supervisor_service.sh
    volumes:
      - ./workspace/{{ bench }}/sites:/workspace/frappe-bench/sites
      - ./configs/.../logs:/workspace/frappe-bench/logs
      - fm-sockets:/fm-sockets       # per-replica supervisord socket (not shared across web replicas)
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:80/api/method/ping"]
      interval: 10s
      timeout: 3s
      retries: 5
    networks: [site-network, global-backend-network, global-frontend-network]

  worker-long:                       # concurrency = supervisor numprocs=background_workers
    image: {{ app_image }}:{{ app_tag }}
    environment:
      SERVICE_NAME: worker-long
    command: launch_supervisor_service.sh
    stop_grace_period: {{ worker_kill_timeout }}s    # graceful stop via supervisor + SIGTERM → (grace) → SIGKILL
    volumes: [ ...sites, logs, fm-sockets... ]
    networks: [site-network, global-backend-network]
```

Every app service pins the same tag and runs its one program under its own `supervisord` via `launch_supervisor_service.sh`; workers scale via supervisor `numprocs = background_workers` (no compose `deploy.replicas`); graceful stop uses supervisor + `stop_grace_period`. For rolling, the web service drops its fixed `container_name` and each web replica gets its **own** `/fm-sockets` supervisord socket (not shared). Volumes are data-only; networks unchanged from `docker-compose.tmpl`.

**C. Provision-then-copy (no build logic in the Dockerfile).**

Step 1 — fm provisions the bench with its existing engine — `BenchApp.install_apps(use_run=True)` + `AppCloner` via `BenchOrchestrator` (`_container_run(use_run=True)` → one-shot container) — into a build context: clone apps (options + hooks), `uv python install` + `uv venv env`, `fnm install`, `uv pip install -e`, `bench build`. **Byte-identical to dev `create`'s provisioning**; only `use_run=True` + the target dir differ.

Step 2 — a minimal `runtime.Dockerfile` just copies the result:

```dockerfile
FROM {{ base_image }}
RUN <curl + runtime libs only> && <create frappe user>
# the whole provisioned tree: apps, env/ venv, .uv/ CPython, .fnm/ node, built assets
COPY --chown=frappe ./context/frappe-bench /workspace/frappe-bench
COPY --chown=frappe ./context/opt/uv-tools /opt/uv-tools   # bench (uv tool)
USER frappe
WORKDIR /workspace/frappe-bench
ENTRYPOINT ["/entrypoint.sh"]
```

No `RUN` build steps, no BuildKit secrets in the Dockerfile (the token was used only by the provisioning `docker run`); relocatable uv Python + venv run as-is.

**No second build recipe / no duplicated nginx config, and no invented Dockerfiles.** All build logic lives *once* in provisioning (fm code, shared by dev `create` + `bake`); the packaging Dockerfiles live in the existing `Docker/` dir and carry zero logic. `Docker/frappe/runtime.Dockerfile` is COPY-only over the base (above). Assets **reuse the existing `Docker/nginx/Dockerfile`** via an appended build target — *not* a new file, *not* a re-declared config:

```dockerfile
# Docker/nginx/Dockerfile (append) — reuse the stock nginx image, add only this tag's baked assets
FROM frappe-manager-nginx AS app-assets       # the image the existing stages already produce (single source of the nginx config)
COPY --chown=... ./context/frappe-bench/sites/assets /usr/share/nginx/html/assets
```

At bake, fm builds `-f Docker/nginx/Dockerfile --target app-assets` with the provisioned assets in context → per-tag nginx, same config/entrypoint. Net maintained: fm's base + nginx recipes (already in `Docker/`, unchanged apart from this 2-line target) + one COPY-only `runtime.Dockerfile` in `Docker/frappe/`. The assets layer exists only for baked-asset rollback atomicity (Decision 2).

These sketches exist to de-risk the design; exact flags/paths are finalized during implementation.

## 17. Worked example (end-to-end)

**Config** (`~/frappe/sites/mybench/bench_config.toml`, additions):

```toml
environment_type = "prod"
runtime = "image"
frappe_server_mode = "gunicorn"

[[apps]]
repo = "frappe/erpnext"
ref = "version-15"

[deploy]
image = "ghcr.io/acme/mybench"
backups = true
rollback = true
drain_workers = true
maintenance_mode_phases = ["migrate"]

[deploy.build]
python_version = "3.12.12"
node_version = "22.20.0"

[deploy.registry]
url = "ghcr.io/acme"
username = "$GHCR_USER"
password = "$GHCR_TOKEN"
distribution = "registry"
```

**Bootstrap + first local deploy:**

```bash
fm create mybench --environment prod --apps erpnext   # provision data/DB/SSL/networks (no app image yet)
fm deploy mybench                                      # = bake → switch
# …or the primitives explicitly:
fm bake mybench --push                                 # → ghcr.io/acme/mybench:fm-<ts>-<sha>
fm switch mybench fm-<ts>-<sha>                        # run the switch pipeline (§8)
```

`fm switch` runs (§8): pre-flight new image → backup DB → maintenance on → fm drains RQ (exec) → `compose stop` workers → migrate (one-shot new-image container) → `compose up -d --wait` new tag (health-gated) → fm resumes RQ → maintenance off → record `[deploy.state]`.

**A bad deploy, then rollback:**

```bash
fm switch mybench fm-<new>    # migrate fails → no swap, old tag kept; re-run to resume (patch_log). DB restore only if restore_on_failure (§8.2)
fm rollback mybench           # or later: switch to [deploy.state].previous, migrate skipped
```

**Remote (ship) from CI or laptop** — add `[deploy.remote]`:

```bash
fm deploy mybench --remote    # registry:  bake --push → ssh remote fm switch <tag>
                              # save_load: bake → docker save | ssh 'docker load' → ssh remote fm switch <tag>
```

The remote leg runs the **same** `switch` pipeline on the target, so drain/migrate/rollback behave identically to local.
