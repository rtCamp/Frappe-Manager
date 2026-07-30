# Configuration Files

Frappe Manager stores settings in TOML configuration files at two scopes:

- **Global**: `~/frappe/fm_config.toml`, machine-wide settings (ngrok tokens, DNS credentials, logging)
- **Per-bench**: `~/frappe/sites/<benchname>/bench_config.toml`, bench-specific settings (environment, SSL, upload limits)

Changes take effect on next `fm start` or service restart.

!!! warning "Use `fm update` commands for safe editing"
    These files are managed by FM. Manual edits may be overwritten or cause validation errors. Use `fm update` commands whenever possible.

!!! tip "Relocating the root directory"
    Set `FRAPPE_MANAGER_HOME` environment variable before any `fm` command to move all FM data to a custom location:
    
    ```bash
    export FRAPPE_MANAGER_HOME=/srv/frappe
    fm create mybench  # Creates bench under /srv/frappe/sites/mybench
    ```

---

## Quick Reference

### Global Config (`fm_config.toml`)

Minimal example showing common settings:

```toml
version = "0.19.0"

ngrok_auth_token = "2abc..."

[validation]
enforce_domain_uniqueness = true

[logs]
file_level = "DEBUG"

[output]
theme = "default"   # default | mono | high-contrast
style = "rail"      # rail | box | flat | ascii

[cloudflare]
api_token = "abc123..."
```

### Bench Config (`bench_config.toml`)

Minimal example showing common settings:

```toml
name = "mybench.localhost"
developer_mode = false
admin_tools = false
environment = "prod"
runtime = "mount"
upload_limit = "50M"
restart_policy = "unless-stopped"
alias_domains = ["www.mybench.com"]
db_name = "fm_mybench_a1b2c3d4"

python_version = "3.13"
node_version = "20"

[auth]
user = "admin"
password = "secret123"
web = true
tools = true

[ssl.dns_challenge_providers.cloudflare]
api_token = "bench-override-token"

[[ssl.certificates]]
domain = "mybench.com"
ssl_type = "letsencrypt"
challenge_type = "http01"
acme_client = "acme.sh"

[migration_state]
migrated_to = "0.19.0"
```

---

## Global Configuration

Settings in `~/frappe/fm_config.toml` apply to all benches and FM operations.

### `version` {#version}

**Default:** (auto-managed)  
**Type:** `string`  
**File key:** `version`

FM version that last wrote this config file. Automatically updated by FM. Do not edit manually.

```toml
version = "0.19.0"
```

---

### `ngrok_auth_token` {#ngrok-auth-token}

**Default:** `null`  
**Type:** `string | null`  
**File key:** `ngrok_auth_token`

Saved ngrok authentication token for persistent tunneling. Written by `fm ngrok --save-token`.

```toml
ngrok_auth_token = "2abc..."
```

**See also:** [fm ngrok command](/commands/ngrok/)

---

### `validation.enforce_domain_uniqueness` {#validation-enforce-domain-uniqueness}

**Default:** `true`  
**Type:** `boolean`  
**File key:** `[validation]` → `enforce_domain_uniqueness`

When `true`, FM prevents creating multiple benches with the same domain. When `false`, allows domain conflicts (use with caution: causes nginx proxy conflicts).

```toml
[validation]
enforce_domain_uniqueness = true
```

!!! warning "Nginx conflicts with duplicate domains"
    Setting this to `false` allows creating benches with duplicate domains, but nginx-proxy can only route to one of them. Use unique domains or `alias_domains` instead.

---

### `logs.file_level` {#logs-file-level}

**Default:** `"DEBUG"`  
**Type:** `string`  
**File key:** `[logs]` → `file_level`

Log verbosity written to `~/frappe/logs/fm.log`. Does not affect console output verbosity (use `--verbose` flag for that).

```toml
[logs]
file_level = "DEBUG"
```

**Valid values:** `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

**See also:** [Logs reference](/reference/logs/)

---

### `cloudflare.api_token` {#dns-providers-cloudflare-api-token}

**Default:** `null`  
**Type:** `string | null`  
**File key:** `[cloudflare]` → `api_token`

Global Cloudflare API Token for DNS-01 SSL challenges. **Recommended over Global API Key** (scoped permissions, more secure).

```toml
[cloudflare]
api_token = "abc123..."
```

!!! tip "Required scoped permissions"
    Your API token needs:
    
    - **Zone → DNS → Edit** for target zones
    - **Zone → Zone → Read** for all zones
    
    Create at: [Cloudflare Dashboard → My Profile → API Tokens](https://dash.cloudflare.com/profile/api-tokens)

!!! info "Per-bench override available"
    Bench-specific tokens in `bench_config.toml` (under `[ssl.dns_challenge_providers.cloudflare]`) take precedence over this global token.

**Set via:** `fm ssl dns-config cloudflare --api-token YOUR_TOKEN`

**See also:** [SSL guide: DNS-01 setup](/guides/ssl/#dns-01-cloudflare-api-token), [fm ssl dns-config command](/commands/ssl/dns-config/)

---

### `cloudflare.api_key` {#dns-providers-cloudflare-api-key}

**Default:** `null`  
**Type:** `string | null`  
**File key:** `[cloudflare]` → `api_key`

Legacy Cloudflare Global API Key for DNS-01 challenges. Requires `email` field. Grants full account access; use `api_token` instead.

```toml
[cloudflare]
api_key = "abc123..."
email = "you@example.com"
```

**Set via:** `fm ssl dns-config cloudflare --api-key YOUR_KEY --email you@example.com`

**See also:** [#dns-providers-cloudflare-api-token](#dns-providers-cloudflare-api-token)

---

### `cloudflare.email` {#dns-providers-cloudflare-email}

**Default:** `null`  
**Type:** `string | null`  
**File key:** `[cloudflare]` → `email`

Cloudflare account email. Required when using `api_key` (not needed for `api_token`).

---

### `output.theme`, `output.style`, `output.colors` {#output}

**Defaults:** `theme = "default"`, `style = "rail"`, `colors = {}`  
**File key:** `[output]` → `theme` / `style` / `colors`

Terminal output appearance for the `fm` CLI.

```toml
[output]
theme = "default"          # default | mono (color-blind safe) | high-contrast
style = "rail"             # rail | box | flat | ascii

[output.colors]
"fm.env.prod" = "bold magenta"   # per-token style overrides
```

**Env overrides:** `FM_THEME` and `FM_STYLE` win over the config file. `NO_COLOR` is honored automatically.

---

### `network.subnet_cidr`, `network.proxy_ip` {#network}

**Default:** `null` (auto-managed)  
**File key:** `[network]` → `subnet_cidr` / `proxy_ip`

Static addressing for the global frontend Docker network: `subnet_cidr` is the CIDR of `fm-global-frontend-network` (e.g. `10.1.0.0/16`), `proxy_ip` the fixed IP of `global-nginx-proxy` on it. Normally written by FM; only set manually if the default subnet collides with your LAN.

---

### `migration_state` {#fm-migration-state}

**File key:** `[migration_state]` → `system_migrated_to`

FM version the global infrastructure was last migrated to. Managed by `fm migrate`; do not edit.

```toml
[migration_state]
system_migrated_to = "0.19.0"
```

## Bench Configuration

Settings in `~/frappe/sites/<benchname>/bench_config.toml` apply to a single bench.

!!! tip "Quick lookup"
    Jump to specific settings: [name](#name) · [developer_mode](#developer-mode) · [admin_tools](#admin-tools) · [environment](#environment-type) · [runtime](#runtime) · [upload_limit](#upload-limit) · [restart_policy](#restart-policy) · [ssl certificates](#ssl-certificates) · [deploy pipeline tables](#deploy-tables)

### `name` {#name}

**Default:** (set on creation)  
**Type:** `string`  
**File key:** `name`

Bench hostname and primary domain. Must be unique if `validation.enforce_domain_uniqueness = true`.

```toml
name = "mybench.localhost"
```

!!! info "Immutable after creation"
    Cannot be changed after creation (requires bench recreation). This value determines:
    
    - Bench directory name: `~/frappe/sites/mybench.localhost/`
    - Container prefix: `fm-mybench-localhost-*`
    - Default URL: `http://mybench.localhost`

---

### `developer_mode` {#developer-mode}

**Default:** `true` (dev environment), `false` (prod environment)  
**Type:** `boolean`  
**File key:** `developer_mode`

Frappe Developer Mode toggle. When `true`, enables debug toolbar, disables caching, shows detailed error pages.

```toml
developer_mode = true
```

!!! tip "Independent from environment type"
    You can enable developer mode in production environments or disable it in dev; this setting is independent of `environment`.

**Change via:** `fm update BENCHNAME --developer-mode enable|disable` (needs an editable workspace: mount runtime)

**See also:** [Environments guide](/guides/environments/), [fm update command](/commands/update/)

---

### `admin_tools` {#admin-tools}

**Default:** `true` (dev environment), `false` (prod environment)  
**Type:** `boolean`  
**File key:** `admin_tools`

Enable Mailpit (email testing) and Adminer (database UI) containers. Protected by HTTP Basic Auth when enabled.

```toml
admin_tools = true
```

**Access URLs (when enabled):**

- Mailpit: `http://<benchname>/mailpit/`
- Adminer: `http://<benchname>/adminer/`

**Change via:** `fm update BENCHNAME --admin-tools enable|disable`

**See also:** [#auth](#auth)

---

### `environment` {#environment-type}

**Default:** `"dev"`  
**Type:** `"dev" | "prod"`  
**File key:** `environment` (legacy key `environment_type` is still read)

Environment profile determining web server, restart policy, and default settings for `developer_mode` and `admin_tools`.

```toml
environment = "prod"
```

| Aspect | `dev` | `prod` |
|---|---|---|
| Web server | Frappe dev server (Werkzeug, hot reload) | Gunicorn (`gthread`, workers = min(CPU cores, RAM/256MB)) |
| Restart policy | `no` (manual start) | `unless-stopped` (auto-recovery) |
| Developer mode | ON by default | OFF by default |
| Admin tools | ON by default | OFF by default |
| Logs | `web.dev.log` (single file) | `web.log` + `web.error.log` (split) |

!!! warning "Switching recreates only the frappe container"
    Environment switch **does not recreate** workers, nginx, Redis, or MariaDB. Only the main frappe web container is recreated.

**Change via:** `fm update BENCHNAME --environment dev|prod`

**See also:** [Environments guide](/guides/environments/)

---

### `upload_limit` {#upload-limit}

**Default:** `"50M"`  
**Type:** `string`  
**File key:** `upload_limit`

Maximum file upload size enforced by nginx. Uses nginx size syntax.

```toml
upload_limit = "500M"
```

**Valid formats:** `50M`, `500M`, `1G`, `2G`

**Change via:** `fm update BENCHNAME --upload-limit 500M`

---

### `restart_policy` {#restart-policy}

**Default:** `"no"` (dev), `"unless-stopped"` (prod)  
**Type:** `"no" | "always" | "on-failure" | "unless-stopped"`  
**File key:** `restart_policy`

Docker Compose restart policy for all bench services (frappe, workers, nginx, Redis).

```toml
restart_policy = "unless-stopped"
```

| Policy | Behavior |
|---|---|
| `no` | Never restart (manual start only) |
| `always` | Always restart (even after manual stop) |
| `on-failure` | Restart only on crash (exit code ≠ 0) |
| `unless-stopped` | Restart unless manually stopped (**recommended for prod**) |

!!! tip "Recommended: `unless-stopped` for production"
    Provides automatic recovery after crashes or server reboots, but respects intentional `fm stop` commands.

**Change via:** `fm update BENCHNAME --restart unless-stopped`

---

### `alias_domains` {#alias-domains}

**Default:** `[]`  
**Type:** `array of strings`  
**File key:** `alias_domains`

Additional hostnames served by this bench. Each alias can have its own SSL certificate.

```toml
alias_domains = ["www.mybench.com", "alt.mybench.com"]
```

**Usage:**

- All aliases serve the same Frappe bench
- Each alias can have independent SSL configuration
- Requires DNS A/CNAME records pointing to server

**Change via:** `fm update BENCHNAME --add-alias www.example.com,alt.example.com` / `--remove-alias www.example.com`

**See also:** [fm ssl add command](/commands/ssl/add/)

---


### `db_name` {#db-name}

**Default:** (auto-generated)  
**Type:** `string`  
**File key:** `db_name`

Database name in global MariaDB service. Auto-generated random string on bench creation to avoid conflicts.

```toml
db_name = "fm_mybench_a1b2c3d4"
```

**Format:** `fm_<benchname_sanitized>_<8_random_chars>`

!!! danger "Do not modify manually"
    Changing this value causes database connection failure. FM expects the database name to match this field exactly.

---

### `github_token` {#github-token}

**Default:** `null`  
**Type:** `string | null`  
**File key:** `github_token`

GitHub Personal Access Token for cloning private app repositories.

```toml
github_token = "ghp_..."
```

**Required permissions:** `repo` (full control of private repositories)

**Set via:** `fm create BENCHNAME --github-token ghp_...` or the `GITHUB_TOKEN` environment variable

**See also:** [fm create command](/commands/create/)

---

### `python_version` {#python-version}

**Default:** (auto-detected from frappe app)  
**Type:** `string | null`  
**File key:** `python_version`

Python version override. Extracted from frappe app's `pyproject.toml` on creation.

```toml
python_version = "3.13"
```

**Set via:** `fm create BENCHNAME --python 3.13` or `fm update BENCHNAME --python 3.13`

**Added in:** FM 0.18.0

---

### `node_version` {#node-version}

**Default:** (auto-detected from frappe app)  
**Type:** `string | null`  
**File key:** `node_version`

Node.js version override. Extracted from frappe app's `pyproject.toml` on creation.

```toml
node_version = "20"
```

**Set via:** `fm create BENCHNAME --node 20` or `fm update BENCHNAME --node 20`

**Added in:** FM 0.18.0

---

### `[auth]` {#auth}

**Default:** admin tools protected, site open  
**File key:** `[auth]`

HTTP basic auth for the bench's two nginx surfaces: `web` (frappe and socketio, every path bar the admin tools) and `tools` (`/adminer/` and `/mailpit/`). One credential pair serves both, and the bench nginx enforces both.

| Key | Default | Meaning |
|---|---|---|
| `user` | `admin` | basic auth username shared by both surfaces |
| `password` | generated | basic auth password; minted on first enable |
| `web` | `false` | prompt on the frappe and socketio surface |
| `tools` | `true` | prompt on the admin tools paths |
| `allow_ips` | `[]` | addresses or CIDRs that skip the prompt |
| `allow_paths` | `[]` | path prefixes served without a prompt, web surface only |

```toml
[auth]
user = "admin"
password = "secret123"
web = true
tools = true
allow_ips = ["203.0.113.0/24"]
allow_paths = ["/api/method/payment_webhook"]
```

**Change via:** `fm auth BENCHNAME --protect web --protect tools`; `fm auth BENCHNAME --status` reports the current state.

!!! warning "Stored in plaintext"
    The password is stored unencrypted in the TOML file, and basic auth sends it base64-encoded on every request. Restrict file permissions and only enable a surface on a bench with TLS:

    ```bash
    chmod 600 ~/frappe/sites/<benchname>/bench_config.toml
    ```

---

### SSL certificates {#ssl-certificates}

**Default:** (none)  
**Type:** `array of tables`  
**File key:** `[[ssl.certificates]]`

List of SSL certificates for the primary domain and aliases. Each domain gets an individual certificate entry under the `[ssl]` table.

```toml
[[ssl.certificates]]
domain = "mybench.com"
ssl_type = "letsencrypt"
challenge_type = "http01"
acme_client = "acme.sh"

[[ssl.certificates]]
domain = "www.mybench.com"
ssl_type = "letsencrypt"
challenge_type = "dns01"
acme_client = "acme.sh"
```

**Certificate fields:**

- `domain`: Hostname for this certificate
- `ssl_type`: `"letsencrypt"` (Let's Encrypt), `"dev"` (locally-trusted dev cert via `fm ssl add --dev`), or `"disable"`
- `challenge_type`: `"http01"` or `"dns01"` (Let's Encrypt only)
- `acme_client`: Always `"acme.sh"`

**Managed by:** `fm ssl add`, `fm ssl remove`, `fm ssl renew` commands

**See also:** [SSL guide](/guides/ssl/), [fm ssl commands](/commands/ssl/)

---

### DNS challenge providers {#dns-providers}

**Default:** (inherits from global config)  
**File key:** `[ssl.dns_challenge_providers.<provider>]`

Bench-specific DNS provider credentials for DNS-01 challenges. Overrides the global `[cloudflare]` table in `fm_config.toml`.

```toml
[ssl.dns_challenge_providers.cloudflare]
api_token = "bench-specific-token"
```

**Override behavior:**

- If set: Uses bench-specific credentials for DNS-01 challenges
- If not set: Falls back to global `fm_config.toml` credentials

**Set via:** `fm ssl dns-config cloudflare BENCHNAME --api-token TOKEN`

**See also:** [#dns-providers-cloudflare-api-token](#dns-providers-cloudflare-api-token), [fm ssl dns-config command](/commands/ssl/dns-config/)

---

### `runtime` {#runtime}

**Default:** `"mount"`  
**Type:** `"mount" | "image"`  
**File key:** `runtime`

Bench runtime model:

| Runtime | Behavior |
|---|---|
| `mount` | App code lives in `workspace/frappe-bench/` on the host and is live-mounted into the containers. Editable; the default for development. |
| `image` | App code is baked into an immutable image (built by `fm bake`); the workspace holds only sites/config. Deploys happen by switching image tags. |

**Change via:** `fm update BENCHNAME --runtime mount` (image → mount). Going mount → image is done with `fm switch` onto a baked image.

**See also:** [Deployment](../deploy/index.md)

---

### `image`, `base_image`, `seed_image` {#images}

**Default:** `null`  
**Type:** `string | null`

| Key | Applies to | Meaning |
|---|---|---|
| `image` | image runtime | App image repository (FM manages the `:tag`, pinned in `[deploy_state]`) |
| `base_image` | mount runtime | Override the base frappe image (`repo:tag`) used for frappe/socketio/schedule/workers |
| `seed_image` | mount runtime | Provenance record: the baked image the workspace was seeded from at create (`fm create --from-image`) |

---

### `[switch]`, `[build]`, `[registry]`, `[deploy]` {#deploy-tables}

Every key that drives the bake/switch pipeline (`fm bake`, `fm deploy`, `fm switch`, `fm prune`). For what the pipeline does with them, see the [Deployment overview](../deploy/index.md).

**`[build]`** (read by `fm bake`):

| Key | Default | Meaning |
|---|---|---|
| `source` | `"provision"` | `provision` = clone + install fresh (reproducible); `workspace` = snapshot the bench's on-disk workspace |
| `base_image` | fm's published base (`ghcr.io/rtcamp/frappe-manager-frappe:v<fm version>`) | the `FROM` / provisioning image |
| `python_version` | the bench's create-time / auto-detected version | Python toolchain (uv) baked into the image |
| `node_version` | the bench's create-time / auto-detected version | Node toolchain (fnm) baked into the image |
| `platform` | native / auto-detected | target architecture (see [Platforms](../deploy/transports.md#platforms-cpu-architectures)) |
| `include` | `[]` | extra host paths baked in (`src` or `src:dest`) |

**`[switch]`** (read by `fm deploy` / `fm switch`):

| Key | Default | Meaning |
|---|---|---|
| `migrate` | `true` | `true` / `false` / `"auto"` (probe the new image against the live DB) |
| `migrate_timeout` | `300` | seconds for the one-shot migrate |
| `migrate_command` | - | custom migrate command override |
| `maintenance_mode` | `true` | show the maintenance page during schema-changing steps |
| `maintenance_mode_phases` | `["migrate"]` | `[]` asserts a backward-compatible migration (enables rolling with migrate) |
| `backup_db` | `true` | `true` / `false` / `"auto"` (dump only when a schema step runs) |
| `rollback_image` | `true` | auto-rollback to the previous tag on a failed health gate |
| `rollback_db` | `false` | also restore the dump when the deploy fails (failed migrate, or alongside the image rollback; requires `backup_db`) |
| `install_apps` | `true` | install newly-baked apps to the site during finalize |
| `keep_releases` | `7` | retention used by `fm prune` |
| `common_site_config` | - | keys merged into `common_site_config.json` during finalize |
| `site_config` | - | keys merged into `site_config.json` during finalize |
| `hooks` | - | `before/after_migrate`, `before/after_restart` (container + `host.*` variants) |

**`[registry]`** (image transport):

| Key | Default | Meaning |
|---|---|---|
| `distribution` | `"registry"` | `"registry"` (push/pull) or `"save_load"` (airgap over SSH) |
| `registry` | - | registry host for `docker login`; omit to use ambient auth |
| `username` | - | login username (env-substituted, e.g. `"${REGISTRY_USER}"`) |
| `password` | - | login password/token (env-substituted, e.g. `"${REGISTRY_TOKEN}"`) |

**`[deploy]`** (remote daemon target, read only by `fm deploy`; `--remote` overrides):

| Key | Default | Meaning |
|---|---|---|
| `ssh_server` | - | remote host to deploy to over `DOCKER_HOST=ssh://` |
| `ssh_user` | `"frappe"` | SSH user |
| `ssh_port` | `22` | SSH port |

---

### `[workers]` {#workers}

Worker care: how `fm restart` and the `fm switch` pipeline treat RQ workers and their in-flight jobs.

| Key | Default | Meaning |
|---|---|---|
| `drain` | `true` | drain RQ workers before cycling (fm restart and fm switch) |
| `drain_timeout` | `300` | seconds to wait for in-flight jobs; restart and switch abort when exceeded |
| `drain_poll` | `5` | poll interval while draining |
| `skip_stale` | `true` | skip idle workers that stop responding |
| `stale_timeout` | `15` | seconds an idle unresponsive worker may block the drain wait |
| `kill_timeout` | `15` | seconds after SIGUSR1 before escalating to a supervisor stop (no-drain path) |
| `kill_poll` | `3.0` | poll interval during the kill wait |

---

### `[deploy_state]` {#deploy-state}

**File key:** `[deploy_state]` + `[[deploy_state.history]]`

Image deploy state, managed by `fm deploy`/`fm switch`; do not edit.

```toml
[deploy_state]
current_tag = "local/mybench:20260728103100-abc123"
previous_tag = "local/mybench:20260721091500-def456"
last_deploy_at = "2026-07-28T10:31:02"

[[deploy_state.history]]
tag = "local/mybench:20260728103100-abc123"
deployed_at = "2026-07-28T10:31:02"
migrate_status = "migrated"      # migrated | skipped | failed | rollback
backup = "/home/user/frappe/sites/mybench/..."  # pre-migrate DB dump, used by `fm switch --previous --restore-db`
```

`fm prune` trims old history rows and their dumps/tags, keeping the newest `keep_releases` (current + previous are always safe).

---

### `[migration_state]` {#migration-state}

**File key:** `[migration_state]`

FM version this bench was last migrated to. Managed by `fm migrate`; do not edit.

```toml
[migration_state]
migrated_to = "0.19.0"
last_migration_date = "2026-04-12T14:30:45"
```

---

### `[monitoring.newrelic]` {#monitoring-newrelic}

**Default:** disabled  
**File key:** `[monitoring.newrelic]` → `enabled` / `license_key`

NewRelic APM for the web (Gunicorn) process.

```toml
[monitoring.newrelic]
enabled = true
license_key = "eu01xx..."
```

**Change via:** `fm update BENCHNAME --newrelic --newrelic-license-key KEY` / `--no-newrelic`

---

## Environment Variables

FM recognizes these environment variables for runtime configuration overrides.

### `FRAPPE_MANAGER_HOME` {#env-frappe-manager-home}

**Default:** `~/frappe`  
**Type:** directory path

Root directory for all FM data (benches, services, logs, configs). Must be set before any `fm` command.

```bash
export FRAPPE_MANAGER_HOME=/srv/frappe
fm create mybench
```

**Relocates all data:**

- Config files: `/srv/frappe/fm_config.toml`
- Benches: `/srv/frappe/sites/<benchname>/`
- Services: `/srv/frappe/services/`
- Logs: `/srv/frappe/logs/`

!!! tip "Make it persistent"
    Add to `~/.bashrc` or `~/.zshrc` to persist across shell sessions:
    
    ```bash
    echo 'export FRAPPE_MANAGER_HOME=/srv/frappe' >> ~/.bashrc
    ```

---

### `FM_LETSENCRYPT_STAGING` {#env-fm-letsencrypt-staging}

**Default:** `"0"`  
**Type:** `"1" | "0" | "true" | "false" | "yes" | "no"`

Force staging Let's Encrypt server for testing. Prevents hitting production rate limits.

```bash
export FM_LETSENCRYPT_STAGING=1
fm ssl add mybench.com
```

!!! warning "Staging certificates are untrusted"
    Staging certificates are issued by "Fake LE Intermediate X1"; browsers will show security warnings. Use only for testing.

**Production rate limits:**

- 50 certificates per registered domain per week
- 5 duplicate certificates per week (same set of names)

**See also:** [SSL guide: Before you start](/guides/ssl/#before-you-start)

---

### Other recognized variables {#env-other}

| Variable | Effect |
|---|---|
| `FM_THEME` | Output color theme override (`default`, `mono`, `high-contrast`); wins over `[output] theme` |
| `FM_STYLE` | Output layout override (`rail`, `box`, `flat`, `ascii`); wins over `[output] style` |
| `NO_COLOR` | Disables colored output entirely |
| `GITHUB_TOKEN` | GitHub token for private app repos (`fm create` / `fm bake`) |
| `NGROK_AUTHTOKEN` | ngrok auth token for `fm ngrok` |
| `FM_DOCKER_IMAGE_TAG` | Override the FM stack image tag (testing only) |

---

## Directory Layout

```
~/frappe/                                    ← FRAPPE_MANAGER_HOME
├── fm_config.toml                           ← Global config
├── logs/
│   └── fm.log                               ← CLI log (10MB rotation, gzipped backups fm.log.1.gz–.3.gz)
├── backups/
│   └── migrations/                          ← Infrastructure migration backups
├── archived/                                ← Benches archived on migration failure
├── services/
│   ├── docker-compose.yml                   ← Global services compose
│   ├── nginx-proxy/
│   │   └── ssl/
│   │       └── acmesh/                      ← acme.sh certificate store
│   ├── mariadb/                             ← MariaDB conf + logs (+ data on Linux)
│   └── secrets/                             ← DB root/user password files
└── sites/
    └── <benchname>/
        ├── bench_config.toml                ← Bench config
        ├── docker-compose.yml               ← Main compose
        ├── docker-compose.workers.yml       ← Workers compose
        ├── docker-compose.admin-tools.yml   ← Admin tools compose
        ├── logs/                            ← Bench logs
        └── workspace/
            └── frappe-bench/                ← Frappe files
```

**See also:** [Architecture reference](/reference/architecture/)
