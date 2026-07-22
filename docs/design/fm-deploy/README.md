# Design: `fm deploy` — Image-Based Deployment (local + remote)

- **Status**: Draft for review
- **Tracking issue**: [rtCamp/Frappe-Manager#323](https://github.com/rtCamp/Frappe-Manager/issues/323)
- **Confirmed decisions**:
  - Image-based deployment lives in **`fm`** (not `fmd`). `fmd` stays release-based.
  - `fm deploy` supports **local host and remote (ship-style)** — remote is first-class, for automation/control parity with the switch pipeline.
  - Distribution: **registry push/pull** *and* **`docker save`/`load` over SSH**.
  - **Two axes** (Decision 11): `environment_type` (`dev`/`prod`) = settings (unchanged); a new `runtime` axis (`mount`/`image`). `image` (default `mount`) is what enables the deploy machinery — existing benches untouched.
- **Companion doc**: `fmd/docs/design/image-based-deployment.md` (the earlier fmd-hosted proposal this supersedes for the deploy path).

> This design is split across the files below. **Section numbers (`§N`) are stable across the set** — a reference like "(§8)" always means section 8 (in `04-runtime-switch.md`), regardless of which file you're reading.

## Contents

| File | Sections |
|---|---|
| [`01-architecture.md`](01-architecture.md) | §1 Why fm · §2 Goals/non-goals (+2.1 divergence from frappe_docker) · §3 `environment_type` architecture (+3.1 execution model, +3.2 command impact) |
| [`02-config.md`](02-config.md) | §4 Config schema (+4.1 config sources & CI portability) |
| [`03-commands-build.md`](03-commands-build.md) | §5 Commands · §6 Image build (bake) |
| [`04-runtime-switch.md`](04-runtime-switch.md) | §7 Prod compose & process model · §8 The `switch` pipeline (+8.1 rollout, +8.2 rollback safety, +8.3 drain/failover via in-container fmx (exec)) |
| [`05-data-remote.md`](05-data-remote.md) | §9 Data layout & state · §10 Remote deploy (ship) |
| [`06-fmd-cutover-decisions.md`](06-fmd-cutover-decisions.md) | §11 Division of labor with fmd · §12 Cutover · §13 Open questions / risks |
| [`07-tasks-debug-appendix.md`](07-tasks-debug-appendix.md) | §14 Task breakdown · §15 `fm code` debug · §16 Appendix (sketches) · §17 Worked example |
| [`08-fc-sync.md`](08-fc-sync.md) | §18 Implementation status (shipped + verified) · §19 Frappe Cloud sync port plan (deferred, needs FC creds) · §20 Minor deferrals |
| [`09-cli-ux.md`](09-cli-ux.md) | §21 UX problem · §22 Decision: `--config` overlay (not fmd's config fork) · §23 Command surface · §24 Standalone bake (planned) · §25 Status |

## Decision record (all locked)

| # | Decision | Choice | Where |
|---|---|---|---|
| 1 | Zero-downtime | **Rolling web swap is the default** (both runtimes), built into fm (docker-rollout algorithm), **not** the plugin. Maintenance page bound to the **migrate step only**: no-schema-change ⇒ zero-downtime, no page; migrate / new-app install ⇒ page. Opt-out (`--additive` / `maintenance_mode_phases=[]`) skips it for asserted backward-compat migrations | §8.1 |
| 2 | Assets | Bake into image (same tag), served by nginx — **reuses `Docker/nginx/Dockerfile`** via an `app-assets` `COPY` target; `runtime.Dockerfile` lives in `Docker/frappe/` (no invented files) | §9 |
| 3 | Drain/migrate/maintenance | Keep in-container `fmx`; fm calls it via `exec` (as `fmd` does); host-side port optional/deferred | §8.3 |
| 4 | Bootstrap | `fm create` provisions (`--environment prod` defaults `runtime=image`); `fm deploy` build/ship/switch-only | §9 |
| 5 | Deploy state | Explicit `[deploy.state]` in `bench_config.toml` | §13 |
| 6 | Worker scaling | Supervisor `numprocs=background_workers` (existing model) | §7 |
| 7 | Multi-site | Single-site per bench | §2 |
| 8 | Registry auth | `docker login` from config creds; ambient fallback | §10 |
| 9 | Tag pruning | Local default; registry opt-in; keep current+previous | §9 |
| 10 | Switch gating | Web healthcheck (`/api/method/ping`) before finalize | §8 |
| 11 | Two-axis model | Separate `runtime: mount\|image` from `environment_type` (settings); default `mount`, backward-compatible | §3 |
| 12 | Debug model | `fm code` = existing supervisor-based flow: VSCode **launches** `bench serve --noreload` under debugpy; `preLaunchTask` `fmx stop frappe` frees port 80. Image: reproduce + observe, isolated data copy, out of rotation | §15 |

Foundational framing: **supervisor STAYS** — each compose service runs its one program under its own `supervisord` (via `launch_supervisor_service.sh`) in both runtimes; the `image` runtime is opt-in **additive** on that model (no supervisor-removal migration). **`fm restart` owns the drain→migrate→restart→resume orchestration over compose; `switch` = `fm restart` + new tag + migrate** (§3.2, §8.3). fm is a plain Docker client managing sibling containers (§3.1) — not DinD, no socket-mounting; remote is the same code with `DOCKER_HOST=ssh://`.
