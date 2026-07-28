# Configuration

Every key that drives the bake/switch pipeline, set in the bench's `bench_config.toml`. For what the pipeline does with them, see the [Deployment overview](index.md); for the wider file format, see [Configuration Files](../reference/configuration.md).

## `[build]`

| Key | Default | Meaning |
|---|---|---|
| `source` | `"provision"` | `provision` = clone + install fresh (reproducible); `workspace` = snapshot the bench's on-disk workspace |
| `base_image` | fm's published base | the `FROM` / provisioning image |
| `python_version` / `node_version` | auto-detected | toolchain baked into the image |
| `platform` | native / auto-detected | target architecture (see [Platforms](transports.md#platforms-cpu-architectures)) |
| `include` | `[]` | extra host paths baked in (`src` or `src:dest`) |

## `[switch]`

| Key | Default | Meaning |
|---|---|---|
| `migrate` | `true` | `true` / `false` / `"auto"` (probe the new image against the live DB) |
| `migrate_timeout` | `300` | seconds for the one-shot migrate |
| `migrate_command` | - | custom migrate command override |
| `maintenance_mode` | `true` | show the maintenance page during schema-changing steps |
| `maintenance_mode_phases` | `["migrate"]` | `[]` asserts a backward-compatible migration (enables rolling with migrate) |
| `backup_db` | `true` | `true` / `false` / `"auto"` (dump only when a schema step runs) |
| `rollback_image` | `true` | auto-rollback to the previous tag on a failed health gate |
| `rollback_db` | `false` | also restore the dump during that auto-rollback (requires `backup_db`) |
| `install_apps` | `true` | install newly-baked apps to the site during finalize |
| `keep_releases` | `7` | retention used by `fm prune` |
| `drain_workers` (+ `_timeout`, `_poll`, `skip_stale_*`) | `true` | drain RQ workers before migrate/swap |
| `common_site_config` / `site_config` | - | keys merged into the site configs during finalize |
| `hooks` | - | `before/after_migrate`, `before/after_restart` (container + `host.*` variants) |

## `[registry]` and `[deploy]`

| Key | Meaning |
|---|---|
| `registry.distribution` | `"registry"` (push/pull) or `"save_load"` (airgap over SSH) |
| `registry.registry` / `username` / `password` | registry host + `docker login` credentials (env-substituted); omit to use ambient auth |
| `deploy.ssh_server` / `ssh_user` / `ssh_port` | remote daemon target (`--remote` overrides) |
