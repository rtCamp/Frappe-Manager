# Configuration Files

Frappe Manager uses a global config and a per-bench config.

Global config: `~/.config/frappe-manager/fm_config.toml` or `/etc/frappe-manager/fm_config.toml`

Example:

```toml
version = "0.1.0"

[letsencrypt]
email = "you@example.com"
api_token = ""
```

Bench config: `~/frappe/sites/<bench>/bench_config.toml`

Example:

```toml
name = "mybench"
environment_type = "dev" # or "prod"
developer_mode = true

[ssl]
ssl_type = "letsencrypt"
cloudflare_api_token = ""
```

After editing bench config files, restart the bench with `fm restart mybench` to apply changes.
