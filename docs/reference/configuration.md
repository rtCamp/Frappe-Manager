# Configuration Files

Frappe Manager stores settings in two main files:

- Global, machine-wide: `~/frappe/fm_config.toml`
- Per-bench: `~/frappe/sites/<bench>/bench_config.toml`

Example global `fm_config.toml`:

```toml
version = "0.19.0"

[cloudflare]
email = "user@example.com"
api_token = "your-scoped-token"
api_key = "your-global-key"

ngrok_auth_token = "your-ngrok-token"

[validation]
enforce_domain_uniqueness = true

[logs]
file_level = "DEBUG"
```

Example per-bench `bench_config.toml`:

```toml
name = "mybench.localhost"
developer_mode = false
admin_tools = false
environment_type = "prod"
upload_limit = "50M"
restart_policy = "unless-stopped"
alias_domains = ["www.mybench.com"]

[[ssl_certificates]]
domain = "mybench.com"
ssl_type = "letsencrypt"
challenge_type = "http01"
acme_client = "acme.sh"
```

!!! warning
    These files are managed by fm. Do not edit them by hand unless you know what you're doing. Use `fm update` for supported changes.
