# Config schema

> Part of the `fm deploy` design set — see [`README.md`](README.md). Section numbers (`§N`) are stable across the set.

## 4. Config schema (extend `BenchConfig`)

Stay in fm conventions: extend `BenchConfig` / `bench_config.toml`. **No** parallel `site.toml`.

```toml
# bench_config.toml

# ─── existing fm fields ───
name = "mybench"
environment_type = "prod"           # SETTINGS axis: developer_mode/admin_tools/restart/dev-pkgs/server default (unchanged meaning)
runtime = "image"                   # RUNTIME axis (new): mount (default) | image. `image` enables everything below
frappe_server_mode = "gunicorn"     # gunicorn (prod default) | bench_serve; independent of both axes
background_workers = 1              # existing fm field → supervisor numprocs per worker (Decision 6 reverted)
# multi_queue_consumption, workers = {…}  → existing fm fields; decide which worker services render

[[apps]]                            # existing fm AppConfig
repo = "frappe/erpnext"
ref = "version-15"

# ─── new: image-based deploy ───
[deploy]
image = "ghcr.io/acme/mybench"      # image repo; fm manages the :tag
migrate = true
maintenance_mode = true
maintenance_mode_phases = ["migrate"]    # page only for schema-changing steps (migrate / new-app install); no schema change → no page (rolling, zero-downtime). [] = assert additive → skip page for a zero-downtime migrate (own the risk)
backups = true                      # pre-switch DB + config backup
rollback = true                     # auto re-pin previous tag on failure
restore_on_failure = false          # opt-in: also restore the DB backup on failed migrate/switch (default off — migrate is transactional/resumable)
search_replace = true
install_apps = true
keep_releases = 7                   # releases `fm prune` keeps; always keeps current + previous

# worker drain → in-container fmx RQ suspend/poll-until-idle, invoked via exec + compose stop_grace_period
drain_workers = true
drain_workers_timeout = 0           # 0 = wait indefinitely (safest for prod)
drain_workers_poll = 5
skip_stale_workers = true
skip_stale_timeout = 15
worker_kill_timeout = 15            # → stop_grace_period on worker services
worker_kill_poll = 3.0

[deploy.build]                      # bake: uv owns Python, fnm owns Node
base_image = "debian:bookworm-slim" # thin OS only; NOT a python image
python_version = "3.12.12"          # uv python install
node_version = "22.20.0"            # fnm install
platforms = ["linux/amd64"]         # target platform(s); cross-arch needs provisioning under emulation
build_args = {}

[deploy.registry]
url = "ghcr.io/acme"
username = "$GHCR_USER"             # env-substituted
password = "$GHCR_TOKEN"            # env-substituted; masked on write
distribution = "registry"          # registry | save_load

[deploy.remote]                     # optional; enables `fm deploy --remote`
host = "192.168.1.100"
ssh_user = "frappe"
ssh_port = 22
remote_path = "/home/frappe/frappe/sites/mybench"

# ─── fm-managed; do NOT hand-edit (Decision 5) ───
[deploy.state]
current = "fm-20260721-a1b2c3d"
previous = "fm-20260718-9f8e7d6"
# history = [ { tag = "…", ts = "…" }, … ]
```

New top-level `BenchConfig` field: `runtime: {mount, image}` (Axis B, Decision 11; default `mount`) — orthogonal to the existing `environment_type`. New nested models (`site_manager/bench_config.py`, or a `deploy_config.py` it imports): `DeployConfig`, `DeployBuildConfig`, `RegistryConfig`, `RemoteConfig`, `DeployStateConfig` — the `[deploy]*` tree is only consulted when `runtime=image`. Worker multiplicity reuses fm's existing `background_workers` / `multi_queue_consumption` / `workers` fields (Decision 6); `AppConfig` reused as-is. `[deploy.state]` is written by fm on each successful switch (current/previous/history) and round-trips like the rest; registry `password` is masked on write per fm's existing secret-masking.

### 4.1 Config sources & CI portability

`bench_config.toml` on the target is the **single canonical source of truth — no separate mandatory config.** But the `[deploy]*` sections are a self-contained, portable subset by design, so the *same schema* can also live in a repo-committed file for CI. All commands accept layered inputs, mirroring fmd's action (`fmd/action.yml`):

- `--config <path>` — a repo-committed base TOML (the `[deploy]` / `[[apps]]` subset). Versioned with the app (GitOps). fm merges it via the same override mechanism fmd uses (`Config.from_toml(overrides=…)`).
- `--config-overrides <toml>` + discrete flags (`--migrate/--no-migrate`, `--drain-workers`, `--platform`, `--image`, …) — per-run overrides for secrets and environment-specific values.
- env substitution (`$GHCR_TOKEN`, `$SSH_*`) — secrets from the CI secret store, never committed.

Precedence: **flags > `--config-overrides` > `--config` file > on-target `bench_config.toml` defaults**.

**Why not a second mandatory config:** two files = two sources of truth = drift — the exact problem fm avoids by owning `bench_config.toml`. And fm has an edge over fmd here: (Decision 4) the bench already exists on the target with its config, so **CI only needs the build spec to `bake`; the switch spec already lives on the target**. The repo file is an *optional GitOps override*, not a parallel runtime config.

**CI parity with fmd.** Ship a composite GitHub Action wrapping `fm bake` / `fm deploy` with the same inputs as `fmd/action.yml` — `gh_token`, `ssh_private_key/server/user/port`, `config_path`, `config_overrides`, and behavior toggles (`migrate`, `drain_workers`, `maintenance_mode[_phases]`, `backups`, `rollback`) — plus image-mode additions (`registry`, `platforms`, `image`). Typical job: `fm bake --config .fm/deploy.toml --push` → `fm deploy --remote` (which SSHes and runs `fm switch <tag>` on the target).
