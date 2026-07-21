# fmd division, cutover & decisions

> Part of the `fm deploy` design set — see [`README.md`](README.md). Section numbers (`§N`) are stable across the set.

## 11. Relationship to `fmd` (fm primary; fmd legacy)

**fm is the primary, feature-complete deploy tool.** Image-based deployment is simply the better model, so rather than maintaining a shared library across two tools, we **port fmd's capabilities directly into fm as first-class features** and let **fmd become legacy — to be deprecated once fm's image path is proven** (not this iteration).

Capabilities to port into fm directly (from fmd), all first-class on fm:

- App sourcing: full clone option set + monorepo reuse + hooks (§6.1).
- Switch pipeline: backup, maintenance, drain, migrate, search-replace, rollback (§8).
- Backup/restore + encryption-key sync (§8.2).
- Frappe Cloud sync (`use_fc_apps`/`use_fc_deps`/`use_fc_db`).
- Remote/ship + transport (registry + `docker save`/`load` over SSH) (§10).
- GitHub Action parity (§4.1).

Within fm, one provisioning path (fmd's `image`/`docker run` runner) is shared by **bake** (provision → `COPY` into the image) and dev `create` (mount) — no separate tool. fmx's drain/migrate/maintenance **stays in-container**; fm calls it via `exec` (as `fmd` does), with a host-side port deferred (§8.3); `bench` commands still run in containers via exec/`docker run`. We do **not** invest in fmd↔fm shared imports; fmd keeps working as-is (release-based) until deprecated.

## 12. Migration path / cutover (phased)

1. **Config + templates**: `deployment_mode` field (default `mount`), `DeployConfig`/`RegistryConfig`/`RemoteConfig`, `frappe_server_mode`; prod compose templates; prod Dockerfile.
2. **Local `bake` + `switch` (+ `deploy` wrapper)**: buildable app image, image runtime, switch/rollback on local host.
3. **Registry + save/load + `deploy --remote`**: transport + remote `switch`.
4. Docs + regenerated CLI docs (`just docs-gen`); `fm code` debug service (#323 item 4). **fmd deprecation** is tracked separately, once fm's image path is proven in production.

## 13. Open questions / risks

- **fm blast radius**: fm is the primary CLI with a migration gate. The image *runtime* is opt-in (`deployment_mode=image`) and **additive** on fm's existing supervisor-per-service model — there is **no all-benches migration** (each compose service still runs its one program under its own `supervisord`). Feature-flag the image path until proven in production.
- **Duplication vs fmd (temporary, accepted)**: fm owns the features directly; fmd stays legacy until deprecated. Accept the short-term overlap rather than building shared fm↔fmd infra.
- **Secrets in build**: private-repo token passed to the provisioning `docker run` (env / secret file), used only at clone, never baked; registry creds env-substituted + masked on write.
- **State source of truth** — **DECIDED: explicit `[deploy.state]` in `bench_config.toml`** (current tag, previous tag, deploy history: tag+timestamp), written transactionally per successful deploy. Reliable rollback target that survives image pruning; retention (`releases_retain_limit`) must always keep the `current`+`previous` tags referenced by state. Deriving from local docker tags rejected (fragile: pruned previous = lost rollback target).
- **Remote execution model** — **DECIDED: prefer `DOCKER_HOST=ssh://`** (local orchestrator drives the remote daemon; no remote fm, no version skew). `ssh remote fm switch` is the fallback and must then pin/verify a compatible `fm` on the remote (mirror fmd's `fmd_source`) (§10).
- **Zero-downtime scope** — **DECIDED: rolling/blue-green web swap is the default** (both runtimes), built into fm (docker-rollout algorithm, not the plugin). Its mechanism (independent of the supervisor model): drop the web service's fixed `container_name` (Docker requires this to run 2 replicas) and give each web replica its **own** per-replica supervisord socket (`/fm-sockets/frappe.sock`, not shared) plus the rolling algorithm. The maintenance page is bound to the **migrate step only**: no schema change ⇒ zero-downtime, no page; `bench migrate`/new-app install ⇒ page. Opt-out `--additive` / `maintenance_mode_phases=[]` skips the page for operator-asserted backward-compatible (expand/contract) migrations (§8.1).
- **Worker scaling** — **DECIDED: supervisor `numprocs=background_workers`** (existing model; each `worker-<queue>` service scales its processes via its own `supervisord`). Compose `deploy.replicas` rejected (would fork process multiplicity away from the established supervisor model) (§7).
- **Assets** — **DECIDED: bake into image + serve via `frappe-app-nginx`** (same tag); sites volume stays pure data; mount site-data subpaths, not the whole `sites/`, to avoid shadowing baked assets (§9).
- **Bootstrap ownership** — **DECIDED: `fm create --environment prod` provisions** (data/DB/SSL/networks, skips app build/run via `environment_type` branch); `fm deploy` stays build/ship/switch-only and errors if the bench isn't created (§9).
- **fmx (drain/migrate/maintenance)** — **DECIDED: keep in-container `fmx`; fm calls it via `exec` (as `fmd` does)** — `fmx restart --drain-workers --migrate --maintenance-mode <phase>` (§8.3). A host-side port of fmx is optional/deferred. Maintenance = `common_site_config`; RQ drain/resume = fmx's `rq_controller`; migrate = `bench migrate`; process lifecycle = supervisor per service.
- **Multi-site scope** — **DECIDED: single-site per bench** (fm's model: bench = one site, `_phase4_create_site`). Backup/migrate/search-replace/maintenance/encryption-key scope to the bench's site; multi-site (`--site all`) is a future non-goal (§2).
- **Registry auth** — **DECIDED: `docker login` from `[deploy.registry]` creds** (env-substituted, `--password-stdin`) before push/pull against the active daemon (incl. remote via `DOCKER_HOST=ssh://`); ambient daemon creds when none configured (§10).
- **Tag pruning** — **DECIDED: prune local tags by default; registry pruning opt-in (`--prune-registry`)**; always preserve `current`+`previous` (and history-referenced) tags (§9).
- **Switch success gating & failure** — **DECIDED**: **pre-flight** the new image (`docker run <tag>` boot check) *before* migrate; **swap via `compose up -d --wait`** gated on the web healthcheck (`/api/method/ping`, §16) before finalize/record. On failure the default is **halt + report** (no destructive DB restore — `bench migrate` is transactional/resumable via `patch_log`, so re-run to resume). **`restore_on_failure`** (opt-in, default off) restores the DB backup + re-pins previous; `rollback` (re-pin tag) and `restore_on_failure` (restore DB) are separate (§8, §8.2).
- **Two-axis model** — **DECIDED (Decision 11): separate `environment_type` (settings, unchanged) from a new `deployment_mode: mount|image` (runtime)** rather than overloading `environment_type`. Backward-compatible (default `mount`; legacy prod keeps working), orthogonal (image-in-dev testable), no silent redefinition of existing prod benches. `--environment prod` only *defaults* `deployment_mode=image` for new benches (§3).
