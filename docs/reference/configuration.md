# Configuration Files

Frappe Manager stores settings in TOML configuration files at two scopes:

- **Global**: `~/frappe/fm_config.toml` — Machine-wide settings (ngrok tokens, DNS credentials, logging)
- **Per-bench**: `~/frappe/sites/<benchname>/bench_config.toml` — Bench-specific settings (environment, SSL, upload limits)

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

[dns_providers.cloudflare]
api_token = "abc123..."
```

### Bench Config (`bench_config.toml`)

Minimal example showing common settings:

```toml
name = "mybench.localhost"
developer_mode = false
admin_tools = false
environment_type = "prod"
upload_limit = "50M"
restart_policy = "unless-stopped"
alias_domains = ["www.mybench.com"]
use_uv = true
db_name = "fm_mybench_a1b2c3d4"

python_version = "3.13"
node_version = "20"

github_token = "ghp_..."

admin_tools_username = "admin"
admin_tools_password = "secret123"

[[ssl_certificates]]
domain = "mybench.com"
ssl_type = "letsencrypt"
challenge_type = "http01"

[dns_providers.cloudflare]
api_token = "bench-override-token"
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

When `true`, FM prevents creating multiple benches with the same domain. When `false`, allows domain conflicts (use with caution — causes nginx proxy conflicts).

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

### `dns_providers.cloudflare.api_token` {#dns-providers-cloudflare-api-token}

**Default:** `null`  
**Type:** `string | null`  
**File key:** `[dns_providers.cloudflare]` → `api_token`  
**Env var:** `FM_CLOUDFLARE_API_TOKEN` (runtime override)

Global Cloudflare API Token for DNS-01 SSL challenges. **Recommended over Global API Key** (scoped permissions, more secure).

```toml
[dns_providers.cloudflare]
api_token = "abc123..."
```

!!! tip "Required scoped permissions"
    Your API token needs:
    
    - **Zone → DNS → Edit** for target zones
    - **Zone → Zone → Read** for all zones
    
    Create at: [Cloudflare Dashboard → My Profile → API Tokens](https://dash.cloudflare.com/profile/api-tokens)

!!! info "Per-bench override available"
    Bench-specific tokens in `bench_config.toml` take precedence over this global token.

**See also:** [SSL guide — DNS-01 setup](/guides/ssl/#dns-01-cloudflare-api-token), [fm ssl dns-config command](/commands/ssl/dns-config/)

---

### `dns_providers.cloudflare.api_key` {#dns-providers-cloudflare-api-key}

**Default:** `null`  
**Type:** `string | null`  
**File key:** `[dns_providers.cloudflare]` → `api_key`

Legacy Cloudflare Global API Key for DNS-01 challenges. Requires `email` field. Grants full account access — use `api_token` instead.

```toml
[dns_providers.cloudflare]
api_key = "abc123..."
email = "you@example.com"
```

**See also:** [#dns-providers-cloudflare-api-token](#dns-providers-cloudflare-api-token)

---

### `dns_providers.cloudflare.email` {#dns-providers-cloudflare-email}

**Default:** `null`  
**Type:** `string | null`  
**File key:** `[dns_providers.cloudflare]` → `email`

Cloudflare account email. Required when using `api_key` (not needed for `api_token`).

```toml
[dns_providers.cloudflare]
api_key = "abc123..."
email = "you@example.com"
```

---

## Bench Configuration

Settings in `~/frappe/sites/<benchname>/bench_config.toml` apply to a single bench.

!!! tip "Quick lookup"
    Jump to specific settings: [name](#name) · [developer_mode](#developer-mode) · [admin_tools](#admin-tools) · [environment_type](#environment-type) · [upload_limit](#upload-limit) · [restart_policy](#restart-policy) · [ssl_certificates](#ssl-certificates) · [dns_providers](#dns-providers)

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
    You can enable developer mode in production environments or disable it in dev — this setting is independent of `environment_type`.

**Change via:** `fm update BENCHNAME --developer-mode enable|disable`

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

**See also:** [#admin-tools-username](#admin-tools-username), [#admin-tools-password](#admin-tools-password)

---

### `environment_type` {#environment-type}

**Default:** `"dev"`  
**Type:** `"dev" | "prod"`  
**File key:** `environment_type`

Environment profile determining web server, restart policy, and default settings for `developer_mode` and `admin_tools`.

```toml
environment_type = "prod"
```

| Aspect | `dev` | `prod` |
|---|---|---|
| Web server | Werkzeug (single-threaded) | Gunicorn (`(CPU×2)+1` workers) |
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

**Change via:** `fm update BENCHNAME --restart-policy unless-stopped`

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

**Change via:** `fm update BENCHNAME --alias-domains www.example.com,alt.example.com`

**See also:** [fm ssl add command](/commands/ssl/add/)

---

### `use_uv` {#use-uv}

**Default:** `true`  
**Type:** `boolean`  
**File key:** `use_uv`

Use `uv` for Python package installation (faster than pip). Falls back to `pip` if `uv` fails.

```toml
use_uv = true
```

**Performance impact:**

- `true`: 5-10× faster installs, automatic fallback to pip on failure
- `false`: Standard pip installs only

**Added in:** FM 0.17.0

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

**Set via:** `fm create BENCHNAME --github-token ghp_...`

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

### `admin_tools_username` {#admin-tools-username}

**Default:** `null` (generated on enable)  
**Type:** `string | null`  
**File key:** `admin_tools_username`

HTTP Basic Auth username for Mailpit and Adminer. Auto-generated when `admin_tools` enabled.

```toml
admin_tools_username = "admin"
```

**See also:** [#admin-tools](#admin-tools), [#admin-tools-password](#admin-tools-password)

---

### `admin_tools_password` {#admin-tools-password}

**Default:** `null` (generated on enable)  
**Type:** `string | null`  
**File key:** `admin_tools_password`

HTTP Basic Auth password for Mailpit and Adminer. Auto-generated when `admin_tools` enabled.

```toml
admin_tools_password = "secret123"
```

!!! warning "Stored in plaintext"
    This password is stored unencrypted in the TOML file. Restrict file permissions:
    
    ```bash
    chmod 600 ~/frappe/sites/<benchname>/bench_config.toml
    ```

---

### `ssl_certificates` {#ssl-certificates}

**Default:** `[]`  
**Type:** `array of objects`  
**File key:** `[[ssl_certificates]]`

List of SSL certificates for primary domain and aliases. Each domain gets individual certificate entry.

```toml
[[ssl_certificates]]
domain = "mybench.com"
ssl_type = "letsencrypt"
challenge_type = "http01"
acme_client = "acme.sh"

[[ssl_certificates]]
domain = "www.mybench.com"
ssl_type = "letsencrypt"
challenge_type = "dns01"
acme_client = "acme.sh"
```

**Certificate object fields:**

- `domain`: Hostname for this certificate
- `ssl_type`: `"letsencrypt"` or `"none"`
- `challenge_type`: `"http01"` or `"dns01"`
- `acme_client`: Always `"acme.sh"`

**Managed by:** `fm ssl add`, `fm ssl remove`, `fm ssl renew` commands

**See also:** [SSL guide](/guides/ssl/), [fm ssl commands](/commands/ssl/)

---

### `dns_providers` {#dns-providers}

**Default:** (inherits from global config)  
**Type:** `object | null`  
**File key:** `[dns_providers]`

Bench-specific DNS provider credentials. Overrides global `fm_config.toml` DNS settings.

```toml
[dns_providers.cloudflare]
api_token = "bench-specific-token"
```

**Override behavior:**

- If set: Uses bench-specific credentials for DNS-01 challenges
- If not set: Falls back to global `fm_config.toml` credentials

**Set via:** `fm ssl dns-config cloudflare BENCHNAME`

**See also:** [#dns-providers-cloudflare-api-token](#dns-providers-cloudflare-api-token), [fm ssl dns-config command](/commands/ssl/dns-config/)

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
    Staging certificates are issued by "Fake LE Intermediate X1" — browsers will show security warnings. Use only for testing.

**Production rate limits:**

- 50 certificates per registered domain per week
- 5 duplicate certificates per week (same set of names)

**See also:** [SSL guide — Before you start](/guides/ssl/#before-you-start)

---

## Directory Layout

```
~/frappe/                                    ← FRAPPE_MANAGER_HOME
├── fm_config.toml                           ← Global config
├── logs/
│   └── fm.log                               ← CLI log (rotated daily)
├── services/
│   ├── docker-compose.yml                   ← Global services compose
│   ├── nginx-proxy/
│   │   └── ssl/
│   │       └── acmesh/                      ← acme.sh certificate store
│   ├── global-db/                           ← MariaDB data
│   └── global-redis-*/                      ← Redis data
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
