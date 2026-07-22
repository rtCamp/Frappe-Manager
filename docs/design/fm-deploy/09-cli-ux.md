# CLI UX/DX — config surface & command ergonomics

> Part of the `fm deploy` design set — see [`README.md`](README.md). Section numbers (`§N`) are stable across the set.

## 21. Problem

Advanced image-deploy config (hooks, `[build]` versions, `common_site_config`/`site_config`, `[fc]`, drain tuning) had **no CLI surface** — you had to hand-edit the server-side `~/frappe/sites/<bench>/bench_config.toml`. There was also no repo/CI path for config, and no standalone image build.

## 22. Decision — overlay, don't fork the config model

fm is **bench-centric**: `bench_config.toml` already *is* the deploy config (fm reads/writes it, every command targets a named bench). fmd uses a standalone `--config` file because it owns no persistent per-bench state — copying that wholesale would create a **second config system competing with `bench_config.toml`** (the mismatch flagged for the GH Action in §4.1).

**Decided:** keep `bench_config.toml` as the single source of truth; add a `--config` **overlay** (not a parallel config).

- First-class **flags** stay for the common 80% (`--image`, `--push`, `--remote`, `--rolling`, `--registry`, versions).
- `--config <file|content>` supplies the structured long tail (hooks, `[build]`, `[fc]`, config-merge) **and** the CI/GitOps path (a repo-committed overlay).

### 22.1 `--config` semantics (shipped)

`fm bake` and `fm deploy` take a **repeatable** `--config`:

- Each value is a **path to a TOML file** *or* **inline TOML content** (auto-detected: existing file → read it; else parse as inline).
- Multiple `--config` **deep-merge left-to-right, later wins** — nested tables merge, scalars/lists overwrite.
- The merged result is **persisted** into `bench_config.toml` (predictable — the bench config reflects exactly what was deployed; the repo overlay stays the source in CI and is re-applied each run).
- Validated on load (`import_from_toml` + `extra="forbid"`) → a typo'd key fails with a clear error.
- Secrets: write `${ENV_VAR}` refs (resolved use-time by registry/transport), so nothing resolved lands in the file.

```bash
# repo-committed base + an inline override (later wins)
fm deploy mysite --config deploy/prod.toml --config 'deploy.migrate = false'

# one-off advanced config without touching the server toml
fm deploy mysite --config 'deploy.before_restart = "./scripts/warm.sh"'
```

Implementation: `frappe_manager/site_manager/deploy_config_overlay.py` (`resolve_source`, `deep_merge`, `merge_overlays`, `apply_config_overlays`); wired into `commands/bake.py` + `commands/deploy.py` before the bench loads.

### 22.2 Explicitly not doing
- A separate always-required `--config` file as the primary interface (forks the bench-centric model).
- Multi-file config *layering* with base + N overrides + precedence rules (fmd's full model) — one repeatable `--config` covers it.
- `--set key=val` — folded into inline `--config 'a.b = "c"'` (dotted keys), so there's one mechanism, not two.

## 23. Command surface

| Command | Purpose | Key flags |
|---|---|---|
| `fm create <bench>` | provision bench + site (mount or image) | `--environment` `--apps` `--image` `--runtime` `--registry` `--distribution` `--python` `--node` |
| `fm bake <bench>` | build image from the bench | `--image` `--tag` `--push/--no-push` `--config` |
| `fm deploy <bench>` | bake + switch (recreate/rolling) | `--image` `--tag` `--remote` `--push/--no-push` `--rolling/--no-rolling` `--config` |
| `fm switch <bench> <tag>` | switch to an existing tag | `--rolling/--no-rolling` |
| `fm rollback <bench>` | re-pin the previous tag | — |

**Model (state this in `--help`):** `create` = provision bench + site; `bake`/`deploy` = build an image *from that bench* + switch. Apps enter via `create --apps` (or `--config` with `apps_list`); `bake` derives them from the created bench.

## 24. Standalone `fm bake` (planned — next slice)

For CI "build once → push → deploy elsewhere" with **no bench/compose/site**:
```bash
fm bake --apps erpnext:version-15 --image ghcr.io/acme/mysite --python 3.12 --push
# or fully via overlay:
fm bake --config ci/build.toml            # [deploy].image + apps_list + [build]
```
Feasible + small because bake's build is already compose-free (`docker run` provision into a temp context + `docker build`); the only coupling is `_derive_apps_list` reading a created bench. Plan: make `benchname` optional; when apps come from `--apps`/`--config`, build a transient `BenchConfig`, skip `_derive_apps_list`, provision into the temp context, build + push. See `08-fc-sync.md` §18 for current status.

## 25. Status
- **Shipped + verified:** `--config` overlay on `bake`/`deploy` (§22.1).
- **Next:** standalone `fm bake` (§24); then help-text/model clarification + a generated commented `[deploy]`/`[build]` template.
