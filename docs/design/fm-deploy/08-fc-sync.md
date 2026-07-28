# Frappe Cloud sync — port plan + implementation status

> Part of the `fm deploy` design set — see [`README.md`](README.md). Section numbers (`§N`) are stable across the set.
>
> This file is the **resume-from-here** doc: §18 records what's already shipped and verified; §19 is the concrete plan to port Frappe Cloud sync (the last substantial gap); §20 lists the remaining minor deferrals.

## 18. Implementation status (what is done)

All of the below is **implemented, unit-tested, and live-verified** on `frappe@178.105.214.28` (Frappe 15/16), branch `dockerimage/0`. Every declared `[deploy]`/`[build]` field is now wired.

| Area | Commit | Verified |
|---|---|---|
| `fm create` image-runtime wiring (`--runtime`/`--image`/`--registry`/`--distribution` → `runtime` + `[deploy]`/`[build]`/`[registry]`); image is opt-in, `--environment prod` stays backward-compatible `mount` | `6cd6cfa` (+ docs `6b50614`) | create→toml round-trip; proven flow it automates |
| `install_apps` in finalize — installs baked apps not yet on the site (`bench list-apps` diff; defensive: skip unless `frappe` in the parsed set) | `015e433` | added `payments` → deploy installed it (`frappe`→`frappe+payments`) |
| Switch hooks — `host_before_restart`/`before_restart` before swap, `after_restart`/`host_after_restart` in finalize | `86bccfd` | all 4 fired in order; env expanded; markers |
| Config-merge — `[deploy].common_site_config`/`site_config` merged in finalize (preserves existing keys) | `8465d95` | merged, db/redis keys preserved, ping 200 |
| Build hooks — 8 `{before,after}_{python_install,bench_build}` + host variants in `provision()` (shared by create+bake); guarded so no-hook path is untouched | `b1e0e60` | fired in bake in fmd order; create fast-path intact |
| `[build].python_version`/`node_version` override in bake (`BakeManager.apply_build_overrides`); `platforms` warns (multi-arch deferred) | `e30f61d` | default `3.12.13` → override `3.11.15` in baked venv |
| Bug fix — python install-detect read `stdout[0]` (entrypoint noise) → corrupt symlink; now scans `combined` for `cpython-X.Y.Z` | `a05ba77` | non-default-minor python installs now work |

**Shared hook helpers** live in `frappe_manager/site_manager/hooks.py` (`resolve_hook_content`, `hook_env`, `hook_script`, `has_build_hooks`, `HOOK_FIELDS`, `BUILD_HOOK_FIELDS`) — used by both the deploy switch-hooks and the provision build-hooks.

## 19. Frappe Cloud sync — port plan (DEFERRED, needs FC creds to verify)

**Goal:** import app set / Python version / DB from a Frappe Cloud site, matching fmd's `use_fc_apps` / `use_fc_deps` / `use_fc_db`. Reference implementation: `~/xde/fm/fmd/fmd/fc/` + `fmd/config/fc.py` + `fmd/managers/release.py`.

**Why deferred:** it is **not scaffolded in fm** (no `use_fc_*` fields, no FC client) and **cannot be e2e-verified without real Frappe Cloud credentials + a source FC site** (API auth, endpoint shapes, and a *destructive* DB restore). Every other gap this session was proven live; this one must be too before it ships.

### 19.1 Config (in `bench_config.py`)
- New `FCConfig(BaseModel)` — `api_key`, `api_secret`, `site_name`, `team_name` (all required). New `[fc]` table; add `fc: FCConfig | None` to `BenchConfig` (+ `import_from_toml`/`export_to_toml` — note: **exclude api_key/api_secret from export or keep as `${ENV}` refs**; fm's registry creds already use use-time `os.path.expandvars`, mirror that so secrets never persist resolved).
- `DeployBuildConfig`: add `use_fc_apps: bool = False`, `use_fc_deps: bool = False` (build-time).
- `DeployConfig`: add `use_fc_db: bool = False` (deploy-time). (`search_replace`, already declared, becomes live here — see 19.4.)

### 19.2 Client + data source (port from fmd, adapt to fm)
New `frappe_manager/site_manager/fc.py` (or `fc/` package) porting **verbatim** (battle-tested):
- `FrappeCloudClient` — `BASE_URL = https://frappecloud.com/api/method`, token header `Authorization: token {api_key}:{api_secret}` + `X-Press-Team: {team_name}`; methods `get_bench_group`, `get_dependencies`, `get_apps_list`, `get_latest_backup_download_urls` (all POST `press.api.client.get_list` / `run_doc_method`). Exact payloads: `fmd/fc/client.py:59-157`.
- `FCDataSource` — `get_apps() -> list[AppConfig]`, `get_python_version() -> str|None`, `download_db_backup(dest_dir) -> Path` (`fmd/fc/data_source.py`).
- **fm AppConfig adaptation:** fmd's `fc_app_to_appconfig` returns `{"ref": hash, "repo": "owner/repo"}`. Map to fm's `AppConfig` — confirm fm's `AppConfig` accepts `repo`+`ref` (or build via `AppConfig.from_string(f"{repo}:{ref}")`). This is the one non-verbatim bit.
- `requests` is used for the HTTP + streamed backup download — confirm it's a dependency (fmd guards it with a stub; fm may already vendor it).

### 19.3 Wiring — build time (`use_fc_apps` / `use_fc_deps`)
In `BakeManager` (mirror `fmd/managers/release.py:48-86` `_get_merged_apps_list`):
- `use_fc_apps`: after `_derive_apps_list()`, fetch `FCDataSource.get_apps()`; merge by repo (override local `ref` with FC commit hash for matching repos, append FC-only apps). Log the merge count.
- `use_fc_deps`: in `apply_build_overrides` (or before provision), if `get_python_version()` and no explicit `[build].python_version`, set it. (Reuses the now-fixed install-detect path — a non-default minor from FC exercises exactly the `a05ba77` fix.)
- Wrap both in try/except → warn + continue (fmd does; a transient FC outage shouldn't hard-fail bake).

### 19.4 Wiring — deploy time (`use_fc_db`)
In `DeployOrchestrator` (mirror `fmd/managers/release.py:432-438`), during the maintenance window (it's a schema-changing restore):
- `use_fc_db`: `FCDataSource.download_db_backup(bench_path/backups/fc-db)` → restore via `MariaDBManager.db_import` (the `_restore_db` path already exists) → **then run `search_replace`** (currently a same-site no-op, but a FC restore brings the *source* site's URLs, so this is where domain rewrite matters — wire `bench --site <site> set-config` / frappe's `scrub`/search-replace to the target domain).
- This is the **destructive** path: only ever touches the deploy target's DB, never the FC source. Gate + log clearly.

### 19.5 Verification (the blocker)
**Prerequisites to obtain before this can ship:** FC `api_key` + `api_secret` (team token), `team_name`, and a **throwaway source FC `site_name`**.
- Unit tests (no creds): mock `requests`; assert `FrappeCloudClient` payloads/parsing, `fc_app_to_appconfig` → fm `AppConfig`, `FCDataSource` shape handling, and the app-merge (override ref by repo, append new).
- E2E (needs creds), on `frappe@178.105.214.28`: create a disposable `fctest.localhost` image bench; set `[fc]` + `use_fc_apps`/`use_fc_deps`; `fm deploy` → assert baked apps match FC (commit hashes) + venv python matches FC deps. Then set `use_fc_db`; `fm deploy` → assert the FC DB restored + search-replace rewrote the domain + site pings 200. Clean up bench/images/DB.

## 20. Remaining minor deferrals (low value)
- **Multi-arch manifests** — deferred (needs per-arch emulated provisioning + buildx imagetools assembly, registry-only). Single-target cross-arch IS wired: `[build].platform` (or fm deploy's remote-arch auto-detection) drives `DOCKER_DEFAULT_PLATFORM` through the whole bake.
- **SSH-fallback remote** (`ssh <host> fm switch` + `[remote].fm_source`) — `DOCKER_HOST=ssh://` (primary) is wired and sufficient; fallback intentionally deferred (§10).
- **Worker force-kill timeouts** (`worker_kill_timeout`/`skip_stale_timeout`/`worker_kill_poll`) — vestigial from fmd; fm's drain is best-effort suspend (swap recreates workers). Wiring would add a kill phase; low value.
- **`search_replace` standalone** — currently a no-op for same-site deploys; becomes meaningful only with `use_fc_db` (19.4).
- **CI: GitHub Action + config layering** — fmd's Action depends on repo-level config layering (`--config`/`--config-overrides`), which fm (bench-centric) doesn't have. If wanted, prefer a **bench-centric Action** (`fm deploy <bench> --remote`) over porting config layering.
