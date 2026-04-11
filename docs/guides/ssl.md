# SSL / HTTPS

This guide explains how to get TLS certificates for your bench. It covers the common HTTP01 method and the Cloudflare-only DNS01 method.

HTTP01 (recommended when DNS points to your machine)

1. Make sure your domain points to your server and ports 80/443 are open.

2. Run:

```bash
fm ssl add mybench example.com --challenge http01 --letsencrypt-email you@example.com
```

3. Test renewal (dry-run):

```bash
fm ssl renew mybench example.com --dry-run
```

DNS01 (Cloudflare only)

1. Add Cloudflare API token to global config (`fm_config.toml`) or bench config:

```toml
[letsencrypt]
cloudflare_api_token = "your-token-here"
```

2. Run with DNS challenge:

```bash
fm ssl add mybench example.com --challenge dns01 --dns-provider cloudflare
```

Configure Cloudflare credentials per-bench by adding them to `bench_config.toml` under `[ssl]`.

SSL during create

```bash
fm create mybench --alias-domains example.com --ssl letsencrypt --letsencrypt-email you@example.com
```

Add SSL to existing bench

```bash
fm update mybench --ssl letsencrypt --letsencrypt-email you@example.com
```

Renewal and removal

```bash
# Renew one
fm ssl renew mybench example.com

# Renew all
fm ssl renew mybench --all

# Remove certificate
fm ssl remove mybench example.com --yes
```

!!! tip
    A typical renewal cron is `0 0 1 * * fm ssl renew mybench --all` so certificates are checked monthly.
