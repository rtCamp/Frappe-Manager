# Commands & Image build

> Part of the `fm deploy` design set — see [`README.md`](README.md). Section numbers (`§N`) are stable across the set.

## 5. Commands

Two primitives + one orchestrator, mirroring fmd's `create`/`switch` + `deploy pull/ship` split. Register in `commands/__init__.py`; `bake`, `switch`, `rollback`, `deploy` join the migration **skip** set alongside `stop`/`delete` (`commands/__init__.py:286-288`) since they own migration/lifecycle themselves.

```
# primitives
fm bake <bench>                   # build image (uv+fnm) → tag fm-<ts>-<sha>; optional --push; prints the tag
fm switch <bench> <tag>           # pull/pin tag → run the switch pipeline (§8) → record [deploy.state]
fm rollback <bench>               # = fm switch <bench> <[deploy.state].previous>, migrate skipped

# orchestrator (convenience over the primitives)
fm deploy <bench>                 # local: bake → switch
fm deploy <bench> --image <tag>   # skip bake, switch to an existing tag
fm deploy <bench> --remote        # ship: bake → transport → ssh remote → fm switch (§10)
```

- **`bake`** is build-time: needs source + tokens + a Docker daemon; runs wherever there are build resources — CI, laptop, or the target host via `DOCKER_HOST=ssh://` (§10). Output is a tag; side effect is an optional registry push.
- **`switch`** is run-time: needs the target host (DB, redis, maintenance). It is also the **rollback primitive** — rolling back is just switching to the previous tag, so there is no special rollback mode; `rollback` is a thin alias reading `[deploy.state].previous`.
- **`deploy`** just chains `bake → switch`; `--remote` chains them across SSH (§10).

Sub-behaviors reuse existing fm plumbing: `ServicesManager`, `BenchService`, `Bench`/`BenchOrchestrator`, `ComposeFile`, `MariaDBManager`.

## 6. Image build (bake) — templates in fm

Provision via fm's existing container-provisioning path, then package the result into an image — **don't re-implement the build as Dockerfile `RUN` steps.** fm **already** provisions a bench with **`BenchApp`** (`site_manager/modules/bench_app.py`) + **`AppCloner`**, sequenced by `BenchOrchestrator`: clone apps (options + hooks) → `setup_python_and_node_environments` (uv python + `uv venv env` + fnm) → `install_apps` (`uv pip install -e` + `bench setup requirements --node`) → `build` (`bench build`). The exec-vs-one-shot seam is **`BenchApp._container_run(..., use_run=)`**: `use_run=False` execs into the running bench (dev/`mount`), `use_run=True` runs a fresh one-shot container (`bake`). `bake` calls the **same** `install_apps(use_run=True, …)` into a build-context dir, then a **minimal Dockerfile `COPY`s it into a runtime image** and sets user/entrypoint. One provisioning path shared with dev `create`; the Dockerfile carries **zero build logic**. (fmd's release-dir provisioning is the legacy duplicate this supersedes — not shared, not ported.)

Runtime model unchanged: `uv` owns Python, `fnm` owns Node, thin-OS base (no python base image) — `.uv/` CPython + `env/` venv + `.fnm/` node, produced by `BenchApp.setup_python_and_node_environments` exactly as dev `create` does.

**Flow:**

1. **Provision** (fm's `BenchApp.install_apps(use_run=True)` via `BenchOrchestrator`) into `<context>/frappe-bench`: create dir → clone `[[apps]]` (all options + hooks, §6.1) → `uv python install` + `uv venv env` → `fnm install` → `uv pip install -e` per app → `bench build`. **Same code as dev `create`** (only `use_run=True` + the target dir differ); host + container hooks interleave exactly as `create` does.
2. **Package**: a minimal `Docker/frappe/runtime.Dockerfile` — `FROM {{ base_image }}`, `COPY --chown=frappe <context>/frappe-bench /workspace/frappe-bench` (apps, `env/`, `.uv/`, `.fnm/`, assets), `USER frappe`, `ENTRYPOINT ["/entrypoint.sh"]`. No `RUN` build steps — uv Python + venv are relocatable, so the copied tree runs as-is.

Build recipes live in the existing `Docker/` dir, not `templates/`: `Docker/frappe/runtime.Dockerfile` (COPY + user + entrypoint only), and the assets image **reuses `Docker/nginx/Dockerfile`** via an appended `app-assets` target (`FROM` the stock nginx image + `COPY sites/assets` — no new file, no config duplication). `templates/` holds only the per-bench renders (`docker-compose.prod.tmpl`, `entrypoint.sh` dispatch). All build logic stays in provisioning (shared with dev `create`), so these recipes carry zero build logic and can't drift.

`DeployBuildConfig`: `base_image`, `python_version` (uv), `node_version` (fnm), `platforms`. FC `use_fc_deps` can drive `python_version` as for releases.

Tag scheme: `<registry>/<bench>:fm-<UTC-ts>-<git-sha>`; current/previous in `[deploy.state]` (§9). Incremental speed comes from fm's **provisioning caches** (uv, git, fnm), not Docker layer cache (the image is essentially one COPY layer). **Cross-arch caveat**: exec-provisioning runs at host arch, so a foreign-arch image needs provisioning under emulation (or a per-arch provision) before the COPY — fine for single-arch/single-node; flag for multi-arch.

### 6.1 App sourcing & hooks (ported from fmd)

fmd's `[[apps]]` is richer than "repo + ref", and it has a hook system — port both into fm directly. fm owns this in its **provisioning** (the existing `docker run` path), used by **both bake and dev `create`**, so behavior matches; bake just `COPY`s the provisioned result into the image.

**Cloning options** (per app, from `AppConfig` / `clone_app`), all handled by fm's builder:

- `ref` — **branch *or* commit** (different checkout: branch clone vs clone + `fetch --depth 1 <sha>` + checkout; `is_ref_commit` decides).
- `shallow_clone` — `depth=1`.
- `subdir_path` — monorepo app in a subdirectory.
- `symlink` — monorepo: **reuse one clone for multiple apps** from the same `(repo, ref)` (fmd's `clone_map`), symlinking each subdir instead of re-cloning.
- `remove_remote`, `remote_name` — post-clone remote cleanup / origin name.
- private-repo `github_token` → passed to the provisioning `docker run` (env / mounted secret file), used only during clone; **never baked into the image**.

Monorepo clone-reuse (one fetch → many apps) and the branch-vs-commit logic are preserved verbatim by the provisioning step (fm's `docker run` path), driven by the app list; the resulting `frappe-bench` tree is then `COPY`'d into the image.

**Hooks** — fmd has **8 build-phase** (per-app, with global fallback in `[release]`) + **4 switch-phase**, each in a host (`host_`) and in-container variant. Mapping:

| fmd hook | runs around | fm image-mode home |
|---|---|---|
| `before/after_python_install` | `uv pip install -e <app>` | in the **provisioning `docker run`** (then COPY'd into the image) |
| `before/after_bench_build` | `bench build` (assets) | in the **provisioning `docker run`** |
| `host_before/after_python_install`, `host_before/after_bench_build` | build, on the host | **host, interleaved around provisioning** (preserved — no isolated build) |
| `before/after_restart` | restart/migrate, in container | exec in the **new container** during switch (§8 step 7) |
| `host_before/after_restart` | restart/migrate, on host | host, around switch (`DeployOrchestrator`) |

**Hook env** (from `get_script_env`): `BENCH_PATH`, `SITE_NAME`, `APP_NAME`, `APP_PATH`, `APPS`, plus **every config field upper-cased** — passed as env into the provisioning `docker run` (build hooks) and the switch exec (switch hooks), same as fmd today. A hook value is inline shell *or* a path to a `.sh`/`.py` (read + inlined), with `set -e` prepended — keep verbatim so existing fmd configs port unchanged.

**Host build hooks keep their per-app interleaving.** Because provisioning runs through fm's `BenchApp` (`_container_run(use_run=True)` → a one-shot container), not an isolated Dockerfile build, fm orchestrates host + container hooks around each app's steps exactly as `create` does; the image is packaged (COPY) only *after* provisioning finishes. So **all hooks — host and container, build and switch — behave identically to dev `create`**. This is a direct benefit of provision-then-copy over a Dockerfile-`RUN` build (which couldn't interleave host steps).
