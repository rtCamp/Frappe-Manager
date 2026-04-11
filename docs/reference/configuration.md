# Configuration Files

Frappe Manager stores settings in two main files:

- **Global / machine-wide**: `~/frappe/fm_config.toml`
- **Per-bench**: `~/frappe/sites/<bench>/bench_config.toml`

The `~/frappe/` root directory can be relocated by setting the `FRAPPE_MANAGER_HOME` environment variable before running any `fm` command. All paths (benches, services, logs, config) move together.

```bash
# Example: put everything under /srv/frappe instead of ~/frappe
export FRAPPE_MANAGER_HOME=/srv/frappe
fm create mybench
```

!!! warning
    These files are managed by `fm`. Do not edit them by hand unless you know what you are doing. Use `fm update` for supported changes.

---

## Global config: `fm_config.toml`

Full example:

```toml
version = "0.19.0"

ngrok_auth_token = "your-ngrok-token"

[validation]
enforce_domain_uniqueness = true

[logs]
file_level = "DEBUG"

[dns_providers.cloudflare]
api_token = "your-scoped-api-token"
# api_key = "your-global-key"        # legacy, requires email
# email = "you@example.com"          # required with api_key
```

| Field | Description |
|---|---|
| `version` | FM version that last wrote this file (managed automatically) |
| `ngrok_auth_token` | Saved ngrok auth token (written by `fm ngrok --save-token`) |
| `validation.enforce_domain_uniqueness` | When `true` (default), FM prevents creating two benches with the same domain |
| `logs.file_level` | Log verbosity written to `~/frappe/logs/fm.log`: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `dns_providers.cloudflare.api_token` | Global Cloudflare API Token for DNS-01 challenges |
| `dns_providers.cloudflare.api_key` | Global Cloudflare Global API Key (legacy) |
| `dns_providers.cloudflare.email` | Cloudflare account email (required with `api_key`) |

---

## Per-bench config: `bench_config.toml`

Full example:

```toml
name = "mybench.localhost"
developer_mode = false
admin_tools = false
environment_type = "prod"
upload_limit = "50M"
restart_policy = "unless-stopped"
alias_domains = ["www.mybench.com"]
admin_pass = "admin"
use_uv = true
db_name = "fm_mybench_a1b2c3d4e5f6g7h8"

# Set by --github-token on create
github_token = "ghp_..."

# Set by --python / --node on create or update
python_version = "3.13"
node_version = "20"

# Set by fm update --admin-tools enable
admin_tools_username = "admin"
admin_tools_password = "generated-password"

# Set by fm ssl add
[[ssl_certificates]]
domain = "mybench.com"
ssl_type = "letsencrypt"
challenge_type = "http01"
acme_client = "acme.sh"

# Set by fm ssl dns-config cloudflare BENCHNAME (bench-specific override)
[dns_providers.cloudflare]
api_token = "bench-specific-token"
```

| Field | Description |
|---|---|
| `name` | Bench hostname (e.g., `mybench.localhost`) |
| `developer_mode` | Whether Frappe developer mode is on (`true`/`false`) |
| `admin_tools` | Whether admin tools (Mailpit, Adminer) are enabled |
| `environment_type` | `dev` or `prod` |
| `upload_limit` | Maximum file upload size (e.g., `50M`, `500M`, `1G`) |
| `restart_policy` | Docker restart policy: `no`, `always`, `on-failure`, `unless-stopped` |
| `alias_domains` | Additional hostnames served by this bench |
| `admin_pass` | Administrator account password |
| `use_uv` | Use `uv` for Python package installs (default `true`, falls back to pip) |
| `db_name` | Database name in the global MariaDB (auto-generated on create, do not change) |
| `github_token` | GitHub PAT for cloning private app repos |
| `python_version` | Python version override (e.g., `3.13`) |
| `node_version` | Node version override (e.g., `20`) |
| `admin_tools_username` | HTTP Basic Auth username for admin tools |
| `admin_tools_password` | HTTP Basic Auth password for admin tools |
| `ssl_certificates` | List of issued TLS certificates (managed by `fm ssl`) |
| `dns_providers` | Bench-specific DNS provider credentials (overrides global config) |

---

## Directory layout

```
~/frappe/                         ← FRAPPE_MANAGER_HOME (default: ~/frappe)
├── fm_config.toml                ← Global config
├── logs/
│   └── fm.log                    ← CLI log (rotated automatically)
├── services/
│   ├── docker-compose.yml        ← Global services compose file
│   └── nginx-proxy/
│       └── ssl/                  ← acme.sh certificate store
└── sites/
    └── <benchname>/
        ├── bench_config.toml     ← Per-bench config
        ├── docker-compose.yml    ← Main bench compose file
        ├── docker-compose.workers.yml
        ├── docker-compose.admin-tools.yml
        └── workspace/
            └── frappe-bench/
```
