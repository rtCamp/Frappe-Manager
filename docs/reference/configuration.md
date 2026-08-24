# Configuration Files

Frappe Manager stores settings in TOML configuration files at two scopes:

- **Global**: `~/frappe/fm_config.toml`, machine-wide settings (ngrok tokens, DNS credentials, logging)
- **Per-bench**: `~/frappe/sites/<benchname>/bench_config.toml`, bench-specific settings (environment, SSL, upload limits)

Changes take effect on next `fm start` or service restart.

!!! warning "Use `fm update` commands for safe editing"
    These files are managed by FM. Manual edits may be overwritten or cause validation errors. Use `fm update` commands whenever possible.

---

## Quick Reference

### Global Config (`fm_config.toml`)

Minimal example showing common settings:

```toml
version = "0.20.0"

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
db_name = "fm_mybench_localhost_9f4c1a77d0e35b62"

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
migrated_to = "0.20.0"
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
version = "0.20.0"
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

**See also:** [fm ngrok command](../commands/ngrok.md)

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

**See also:** [Logs reference](logs.md)

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

**See also:** [SSL guide: DNS-01 setup](../guides/ssl.md#dns-01-cloudflare-api-token), [fm ssl dns-config cloudflare](../commands/ssl-dns-config-cloudflare.md)

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

**See also:** [`cloudflare.api_token`](#dns-providers-cloudflare-api-token)

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
system_migrated_to = "0.20.0"
```

---

## Bench Configuration

Settings in `~/frappe/sites/<benchname>/bench_config.toml` apply to a single bench.

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

**Default:** `true` (dev environment), `false` (prod environment); always `false` on image runtime  
**Type:** `boolean`  
**File key:** `developer_mode`

Frappe Developer Mode toggle. When `true`, enables debug toolbar, disables caching, shows detailed error pages.

```toml
developer_mode = true
```

!!! warning "Refused on image runtime"
    `developer_mode = true` cannot be combined with `runtime = "image"`. DocType authoring writes app *source* files, and standard doctypes only sync files into the database, never back, so those writes would land in the container's ephemeral layer and be lost on the next deploy. `fm create` rejects both the flag and a config overlay that sets it; demote with `fm update BENCHNAME --runtime mount` first.

**Change via:** `fm update BENCHNAME --developer-mode enable|disable` (needs an editable workspace: mount runtime)

**See also:** [Environments guide](../guides/environments.md), [fm update command](../commands/update.md)

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

**See also:** [`[auth]`](#auth), which puts a password prompt in front of these tools

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
    `fm update -e` recreates the frappe web container alone: workers, nginx, Redis and MariaDB keep running. `developer_mode` and `admin_tools` are left exactly as they are, so the defaults in the table above apply at create time only; change them afterwards with `--developer-mode` or `--admin-tools`.

**Change via:** `fm update BENCHNAME --environment dev|prod`

**See also:** [Environments guide](../guides/environments.md)

---

### `upload_limit` {#upload-limit}

**Default:** `"50M"`  
**Type:** `string`  
**File key:** `upload_limit`

Maximum file upload size. One value drives three layers: `max_file_size` in `site_config.json`, `client_max_body_size` in the bench nginx, and the `vhost.d` entry nginx-proxy applies to every domain of the bench.

```toml
upload_limit = "500M"
```

**Valid formats:** digits followed by `M` or `G`, case-insensitive (`50M`, `500M`, `1G`), stored uppercased. Bare byte counts and a `K` suffix are rejected even though nginx itself accepts them.

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

**Change via:** `fm update BENCHNAME --add-alias www.example.com,alt.example.com` / `--remove-alias www.example.com`

**See also:** [fm ssl add command](../commands/ssl.md)

---

### `db_name` {#db-name}

**Default:** (auto-generated)  
**Type:** `string`  
**File key:** `db_name`

Schema this bench's site uses on the fm-managed `global-db` container. Generated at creation as `fm_<name>_<16 hex chars>`, where `<name>` is the bench name with every `.` and `-` replaced by `_`.

```toml
db_name = "fm_mybench_localhost_9f4c1a77d0e35b62"
```

!!! danger "Do not modify manually"
    Changing this value points the bench at a schema that does not exist. FM expects the database name to match this field exactly.

!!! info "Unused on an external database"
    A site with a [`[database."<site>"]`](#database) entry lives on that server, under `[database]` `name`. `db_name` is still generated and stored, but nothing reads it for that site.

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

**See also:** [fm create command](../commands/create.md)

---

### `python_version` {#python-version}

**Default:** (auto-detected from frappe app)  
**Type:** `string | null`  
**File key:** `python_version`

Python version override. Auto-detected on creation from the frappe app's `pyproject.toml` (`[project]` `requires-python`, or Poetry's `tool.poetry.dependencies.python`).

```toml
python_version = "3.13"
```

**Set via:** `fm create BENCHNAME --python 3.13` or `fm update BENCHNAME --python 3.13`

---

### `node_version` {#node-version}

**Default:** (auto-detected from frappe app)  
**Type:** `string | null`  
**File key:** `node_version`

Node.js version override. Auto-detected on creation from the frappe app's `package.json` (`engines.node`).

```toml
node_version = "20"
```

**Set via:** `fm create BENCHNAME --node 20` or `fm update BENCHNAME --node 20`

---

### `[auth]` {#auth}

**Default:** admin tools protected (when `admin_tools` is enabled), site open  
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

- `domain`: hostname this certificate covers
- `ssl_type`: `"letsencrypt"`, or `"dev"` for a certificate from FM's local CA (`fm ssl add --dev`). `"disable"` is accepted on read and means no certificate; FM never writes such an entry, it drops the domain from the array instead
- `challenge_type`: `"http01"` or `"dns01"`, Let's Encrypt only. Absent, FM picks `dns01` when Cloudflare credentials exist and `http01` otherwise
- `acme_client`: always `"acme.sh"`
- `api_token` / `api_key`: per-certificate DNS credentials, taking precedence over `[ssl.dns_challenge_providers]` and the global `[cloudflare]` table
- `delegation_cname`: delegated zone for `_acme-challenge`, written by `fm ssl add --cname`
- `hsts`: value for the `Strict-Transport-Security` header the proxy sends for this domain, or `"off"` (the default). There is no flag for it; set it here. Only the primary domain's certificate is consulted

FM also writes bookkeeping keys into each entry (`enabled`, `status`, `cert_path`, `key_path`, `issued_date`, `last_renewal_attempt`). `issued_date`, `last_renewal_attempt` and `key_path` are outputs that nothing reads back, so editing them changes nothing.

**Managed by:** `fm ssl add`, `fm ssl remove`, `fm ssl renew`, `fm ssl list`

**See also:** [SSL guide](../guides/ssl.md), [fm ssl commands](../commands/ssl.md)

---

### DNS challenge providers {#dns-providers}

**Default:** (inherits from global config)  
**File key:** `[ssl.dns_challenge_providers.<provider>]`

Bench-specific DNS provider credentials for DNS-01 challenges. Overrides the global `[cloudflare]` table in `fm_config.toml`. Each provider table accepts `api_token`, `api_key` and `email`; `cloudflare` is the only provider name FM feeds to acme.sh.

```toml
[ssl.dns_challenge_providers.cloudflare]
api_token = "bench-specific-token"
```

**Set via:** `fm ssl dns-config cloudflare BENCHNAME --api-token TOKEN`

**See also:** [`cloudflare.api_token`](#dns-providers-cloudflare-api-token), [fm ssl dns-config cloudflare](../commands/ssl-dns-config-cloudflare.md)

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
| `image` | image runtime | App image repository, the pre-built app image the bench runs (FM manages the `:tag`, pinned in `[deploy_state]`). Set by `fm create --runtime image --image` and by `fm bake --image`, which bakes into it |
| `base_image` | mount runtime | The base frappe image (`repo:tag`) the frappe/socketio/schedule/workers containers **run from**. Set by `fm create --base-image`. Not the same key as [`[build].base_image`](#deploy-tables), which is what a bake builds from |
| `seed_image` | mount runtime | Provenance record: the baked image the workspace was seeded from at create (`fm create --from-image`) |

---

### `[switch]`, `[build]`, `[registry]` {#deploy-tables}

Every key that drives the bake/switch pipeline (`fm bake`, `fm switch`, `fm prune`). For what the pipeline does with them, see the [Deployment overview](../deploy/index.md).

**`[build]`** (read by `fm bake`):

| Key | Default | Meaning |
|---|---|---|
| `source` | `"provision"` | `provision` = clone + install fresh (reproducible); `workspace` = snapshot the bench's on-disk workspace, which needs a real bench (a standalone `fm bake` rejects it) |
| `base_image` | fm's published base (`ghcr.io/rtcamp/frappe-manager-frappe:v<fm version>`) | the image the runtime Dockerfile **builds from** during a bake, and the image the provisioning containers run. Set by `fm bake --base-image`. Not the same key as the top-level [`base_image`](#images), which is what a mount bench's containers run from |
| `python_version` | the bench's create-time / auto-detected version | Python toolchain (uv) baked into the image |
| `node_version` | the bench's create-time / auto-detected version | Node toolchain (fnm) baked into the image |
| `platform` | native / auto-detected | target architecture (see [Platforms](../deploy/transports.md#platforms-cpu-architectures)) |
| `include` | `[]` | extra host paths baked in (`src` or `src:dest`) |
| `push` | `false` | push the built image pair to the registry after building. `fm bake --push` / `--no-push` overrides it either way. A bake that does not push still loads the image into the local daemon, so a same-host `fm switch` needs no registry |

**`[switch]`** (read by `fm switch`):

| Key | Default | Meaning |
|---|---|---|
| `migrate` | `true` | `true` / `false` / `"auto"` (probe the new image against the live DB) |
| `migrate_timeout` | `300` | seconds the one-shot migrate may run before it is killed (`0` disables the budget) |
| `migrate_command` | (none) | custom migrate command override |
| `maintenance_mode` | `true` | show the maintenance page during schema-changing steps |
| `maintenance_mode_phases` | `["migrate"]` | `[]` asserts a backward-compatible migration (enables rolling with migrate) |
| `backup_db` | `true` | `true` / `false` / `"auto"` (dump only when a schema step runs) |
| `rollback_image` | `true` | auto-rollback to the previous tag on a failed health gate |
| `rollback_db` | `false` | also restore the dump when the deploy fails (failed migrate, or alongside the image rollback; requires `backup_db`) |
| `install_apps` | `true` | during finalize, install apps the new image carries that the site does not have yet (the image is asked directly, so it works on any switch) |
| `keep_releases` | `7` | retention used by `fm prune` |
| `common_site_config` | (none) | keys merged into `common_site_config.json` during finalize |
| `site_config` | (none) | keys merged into `site_config.json` during finalize |
| `hooks` | (none) | `before/after_migrate`, `before/after_restart` (container + `host.*` variants) |

**`[registry]`** (registry auth, used for both push and pull):

| Key | Default | Meaning |
|---|---|---|
| `registry` | (none) | registry host for `docker login`; omit to use ambient auth |
| `username` | (none) | login username (env-substituted, e.g. `"${REGISTRY_USER}"`) |
| `password` | (none) | login password/token (env-substituted, e.g. `"${REGISTRY_TOKEN}"`) |

!!! warning "An unknown key is a hard error"
    `[switch]`, `[build]`, `[registry]`, `[workers]`, `[auth]`, `[monitoring]`, `[database]` and `[redis]` reject keys they do not define. A misspelled key is not ignored: every `fm` command that loads the bench fails with a validation error until you remove it.

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

### `[database."<site>"]` {#database}

**Default:** (absent)  
**File key:** `[database."<site>"]`, one table per site name

External MariaDB for one site. An absent entry means that site lives on the FM-managed `global-db` container; there is no separate on/off flag.

| Key | Default | Meaning |
|---|---|---|
| `host` | required | database server hostname or IP. Any MariaDB; MySQL is not a supported backend |
| `port` | `3306` | database server port |
| `name` | required | schema (database) name for this site |
| `user` | (none) | login user for the schema; absent means equal to `name`, and it must equal `name` on a v15 bench |
| `ca` | (none) | host path to the CA bundle; required when the server enforces TLS |
| `check_hostname` | `true` | verify the server certificate names the host dialled. Set `false` only for a certificate that cannot name it |

```toml
[database."mybench.localhost"]
host = "db.example.com"
port = 3306
name = "app_prod"
user = "app_prod"
ca = "/etc/ssl/certs/db-ca.pem"
```

Passwords never live here: the site's database password goes into `site_config.json`.

**Set via:** `fm create BENCHNAME --db-host ... --db-name ...`; `fm update BENCHNAME --db-ca` reinstalls the CA after a rotation.

**See also:** [External database guide](../guides/external-database.md)

---

### `[redis]` {#redis}

**Default:** (absent)  
**File key:** `[redis]`

External Redis for the whole bench. Absent means FM starts and manages the per-bench `redis-cache` and `redis-queue` containers.

| Key | Default | Meaning |
|---|---|---|
| `cache` | required | Redis URL for the framework cache |
| `queue` | required | Redis URL for the queue and realtime |

```toml
[redis]
cache = "redis://r.example:6379/0"
queue = "redis://r.example:6379/1"
```

!!! danger "Cache and queue need different logical databases"
    Loading the config fails when they share one. A restore calls `frappe.cache.delete_keys("")`, a mass delete, so a shared index would wipe the queue along with the cache.

**Set via:** `fm create BENCHNAME --redis-cache URL --redis-queue URL`

---

### `[[apps]]` {#apps}

**File key:** `[[apps]]` (the legacy name `[[apps_list]]` is still read)

Apps to install. Read by `fm create --config` and `fm bake --config`, and built from `fm create --apps`. FM never writes this array back, so a bench's own `bench_config.toml` normally has none; the installed apps are whatever the workspace or the image carries, and `fm info` lists them.

| Key | Default | Meaning |
|---|---|---|
| `name` | required | app module name, e.g. `erpnext` |
| `repo` | required | `owner/repo`, or a full HTTPS or `git@` URL |
| `ref` | (none) | branch, tag, or 40-character commit SHA |
| `repo_url` | (derived) | full clone URL; derived from `repo` when absent |
| `shallow_clone` | `true` | clone with `--depth 1` |
| `subdir_path` | (none) | path inside a monorepo holding the app |
| `symlink` | `false` | symlink the monorepo subdirectory instead of copying it |
| `hooks` | (none) | per-app build hooks `before_deps`, `after_deps`, `before_build`, `after_build`, plus a nested `host` table with the same four |

```toml
[[apps]]
name = "erpnext"
repo = "frappe/erpnext"
ref = "version-15"
```

Frappe is installed first whether or not it appears here.

---

### `[deploy_state]` {#deploy-state}

**File key:** `[deploy_state]` + `[[deploy_state.history]]`

Image deploy state, managed by `fm switch`; do not edit.

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
migrated_to = "0.20.0"
last_migration_date = "2026-04-12T14:30:45"
```

---

### `[monitoring.newrelic]` {#monitoring-newrelic}

**Default:** (absent, NewRelic off)  
**File key:** `[monitoring.newrelic]`

NewRelic APM for the web process. The agent is wired into the generated Gunicorn wrapper, `config/fm-web-server.sh`, so it is the `prod` web process that reports; a `dev` bench serves through `bench serve` and never loads it.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | run the web process under the NewRelic agent |
| `license_key` | (none) | NewRelic ingest license key; required whenever `enabled` is `true` |

```toml
[monitoring.newrelic]
enabled = true
license_key = "eu01xx..."
```

`[monitoring]` defines `newrelic` and nothing else, so any other sub-table under it is a validation error.

With both keys set, FM writes `workspace/frappe-bench/config/newrelic.ini` (app name `Frappe - <bench>`, SQL recorded obfuscated, `Authorization` and `Cookie` request headers excluded) and adds `NEWRELIC_ENABLED` and `NEWRELIC_LICENSE_KEY` to the `frappe` service's compose environment. The wrapper installs the `newrelic` package into the bench venv on first start when it is missing, then execs Gunicorn under `newrelic-admin`. If the env vars are set but `newrelic.ini` is gone, the web process refuses to start; run `fm update` to regenerate it.

!!! warning "Enabling without a license key is refused"
    `fm create --newrelic` and `fm update --newrelic` both fail with a parameter error when no key is passed and none is already stored. There is no half-enabled state: the compose env vars and `newrelic.ini` are written only when `enabled` and `license_key` are both set, and the wrapper falls back to plain Gunicorn otherwise.

**Set via:** `fm create BENCHNAME --newrelic --newrelic-license-key KEY`; `fm update BENCHNAME --newrelic --newrelic-license-key KEY` / `--no-newrelic`. `fm update` force-recreates the frappe container to apply the change.

**See also:** [Monitoring (New Relic)](../guides/environments.md#monitoring-new-relic)

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

Everything moves with it; [Where the files live](#directory-layout) shows the tree it produces. Export it from your shell profile to make it stick across sessions.

---

### `FM_LETSENCRYPT_STAGING` {#env-fm-letsencrypt-staging}

**Default:** unset (off)  
**Type:** `1`, `true` or `yes` enables staging; any other value, including unset, leaves it off

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

**See also:** [SSL guide: Before you start](../guides/ssl.md#before-you-start)

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

## Where the files live {#directory-layout}

Only the paths that hold configuration or credentials. The [Architecture reference](architecture.md) has the annotated tree of everything else.

```
~/frappe/                                    ← FRAPPE_MANAGER_HOME
├── fm_config.toml                           ← Global config
├── logs/fm.log                              ← CLI log (rotates at 10 MiB, keeping fm.log.1.gz to fm.log.3.gz)
├── services/
│   ├── docker-compose.yml                   ← Global services compose
│   ├── nginx-proxy/ssl/acmesh/              ← acme.sh certificate store
│   ├── mariadb/conf/                        ← global-db configuration
│   └── secrets/                             ← global-db root and user password files
└── sites/<benchname>/
    ├── bench_config.toml                    ← Bench config
    ├── docker-compose.yml                   ← Main compose
    ├── docker-compose.workers.yml           ← Workers compose
    ├── docker-compose.admin-tools.yml       ← Admin tools compose
    ├── configs/nginx/conf/                  ← Bench nginx config, including the custom/ overlay
    └── workspace/frappe-bench/sites/        ← common_site_config.json and per-site site_config.json
```

Inside `configs/nginx/conf/`, fm owns three files and rewrites them on every `fm start`: `custom/real-ip.conf` from the frontend network subnet, plus the basic-auth server and map confs it derives from [`[auth]`](#auth). Editing those is pointless. Other files you drop into `custom/` are left alone, `custom/upload-limit.conf` among them, which is why `upload_limit` changes go through `fm update` rather than the file.
