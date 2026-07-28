# fm ssl dns-config cloudflare

Configure Cloudflare credentials for DNS-01 certificate challenges. This is required before running `fm ssl add ... --challenge dns01` with Cloudflare as the DNS provider.

Usage:

```console
$ fm ssl dns-config cloudflare [BENCHNAME] [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `--api-token` | Cloudflare API Token (recommended - scoped permissions) |
| `--api-key` | Cloudflare Global API Key (legacy - full account access) |
| `--email` | Cloudflare account email (required when using `--api-key`) |
| `-s, --show` | Display the current stored credentials |
| `-r, --remove` | Delete the stored credentials |

## Credential scope

Credentials can be stored at two levels:

- **Global** (omit `BENCHNAME`) - used as the fallback for all benches
- **Bench-specific** (provide `BENCHNAME`) - overrides global credentials for that bench only

Bench-specific credentials take priority over global ones when both are configured.

## Authentication methods

### API Token (recommended)

API Tokens are scoped to specific zones and permissions, making them much safer than the Global API Key.

1. Go to <https://dash.cloudflare.com/profile/api-tokens>
2. Click **Create Token**
3. Use the **Edit zone DNS** template
4. Required permission: Zone → DNS → Edit
5. Restrict to the specific zone(s) you need

```bash
fm ssl dns-config cloudflare --api-token YOUR_API_TOKEN
```

### Global API Key (legacy)

The Global API Key has full account access. Only use it if API Tokens are not available.

```bash
fm ssl dns-config cloudflare --api-key YOUR_API_KEY --email you@example.com
```

`--email` is required when using `--api-key`.

## Examples

```bash
# Set global Cloudflare credentials (API Token)
fm ssl dns-config cloudflare --api-token YOUR_TOKEN

# Set bench-specific credentials that override global
fm ssl dns-config cloudflare mybench --api-token DIFFERENT_TOKEN

# Use legacy Global API Key
fm ssl dns-config cloudflare --api-key YOUR_API_KEY --email you@example.com

# Show current global credentials
fm ssl dns-config cloudflare --show

# Show credentials for a specific bench
fm ssl dns-config cloudflare mybench --show

# Remove global credentials
fm ssl dns-config cloudflare --remove

# Remove bench-specific credentials
fm ssl dns-config cloudflare mybench --remove
```

After configuring credentials, issue certificates with:

```bash
fm ssl add mybench example.com --challenge dns01
```
