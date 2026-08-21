# SSL / HTTPS

Frappe Manager provides built-in SSL certificate management using Let's Encrypt. You can secure your benches with trusted HTTPS certificates using either HTTP-01 (the default) or DNS-01 (Cloudflare) challenge types.

## Overview

When you run `fm ssl add`, Frappe Manager:

1. **Installs acme.sh** on first use into `~/frappe/services/nginx-proxy/ssl/acmesh/.acme.sh/`
2. **Runs the ACME challenge** (HTTP-01 or DNS-01) to prove domain ownership to Let's Encrypt
3. **Obtains the certificate** and private key
4. **Installs the certificate** into your bench's nginx configuration automatically
5. **Enables HTTPS redirect** so all HTTP traffic redirects to HTTPS
6. **Handles renewal** automatically when certificates are about to expire (< 30 days)

SSL is supported for both **FM-managed benches** and **external Docker projects** sharing FM's nginx-proxy network (standalone mode).

!!! tip "Quick Start"
    **For most users (HTTP-01):**
    ```bash
    # Test first (staging server)
    fm ssl add mybench example.com --dry-run
    
    # Then issue for real
    fm ssl add mybench example.com
    ```
    
    **For Cloudflare users (DNS-01):**
    ```bash
    # Save credentials once
    fm ssl dns-config cloudflare --api-token YOUR_TOKEN
    
    # Then issue
    fm ssl add mybench example.com --challenge dns01
    ```
    
    **Automate renewals:**
    ```bash
    # Add to crontab (renews when < 30 days remain)
    0 3 * * * fm ssl renew --all
    ```

## Challenge Types

FM supports two ways to prove domain ownership to Let's Encrypt:

| | HTTP-01 | DNS-01 |
|---|---|---|
| **How it works** | Let's Encrypt fetches a validation file from `http://yourdomain/.well-known/acme-challenge/` | You (FM) add a TXT record to your DNS zone programmatically |
| **Port 80 required** | ✅ Yes | ❌ No |
| **Wildcard certs** | ❌ No | ✅ Yes |
| **Best for** | Most production setups with public domains | Firewalled servers, wildcard domains, internal infrastructure |
| **Supported DNS providers** | N/A | Cloudflare (built-in support) |
| **Setup complexity** | Simple (default) | Requires API credentials |

**Quick decision guide:**
- ✅ Use **HTTP-01** if your domain points to the server and ports 80/443 are open (simplest, default)
- ✅ Use **DNS-01** if port 80 is blocked, you need wildcard certificates, or testing on internal networks
- ✅ Use `--dev` for local development: issues a locally-trusted certificate from a local CA, no internet or public DNS required (see [below](#local-development-certificates-dev))

!!! note "HTTP-01 and a password prompt coexist; maintenance mode does not"
    `fm auth` puts an HTTP basic auth prompt in front of a bench, but the bench nginx serves `/.well-known/acme-challenge/` with `auth_basic off`, so issuance and renewal are never blocked by it. `fm maintenance` is different: it exempts nothing by default, so if a renewal falls due while the maintenance page is up, allow the path explicitly with `fm maintenance <bench> --allow-path '/.well-known/acme-challenge/*'`.

---

## Before you start

### HTTP-01 checklist

- [ ] Your domain's A record points to this server's public IP
- [ ] Port 80 and 443 are open in your firewall / security group
- [ ] No other process is occupying port 80 on the host

### DNS-01 checklist

- [ ] You have Cloudflare managing the DNS zone for your domain
- [ ] You have created a Cloudflare API Token with **Zone → DNS → Edit** permission
- [ ] DNS credentials are saved with `fm ssl dns-config cloudflare`

FM validates that the domain's DNS resolves before issuing. If you intend to configure DNS later, pass `--skip-dns-check` to `fm ssl add`.

!!! warning "Always dry-run first"
    Run with `--dry-run` before issuing real certificates. The dry-run uses the Let's Encrypt **staging** server; it validates your setup without consuming your [rate limit quota](https://letsencrypt.org/docs/rate-limits/) (50 certificates per registered domain per week, 5 per identical set of names per week).

---

## HTTP-01 setup

### 1. Dry run

Validates that Let's Encrypt can reach your server and complete the challenge:

```bash
fm ssl add mybench example.com --dry-run
```

Expected output: a message like `Cert verified OK` or acme.sh's staging success banner. If it fails, see [Troubleshooting](#troubleshooting) below.

### 2. Issue the certificate

```bash
fm ssl add mybench example.com
```

FM will:

1. Start the acme.sh HTTP-01 challenge handler inside the FM nginx-proxy
2. Obtain the certificate from Let's Encrypt
3. Install it into the bench's nginx configuration
4. Reload nginx

### 3. Verify

```bash
fm ssl list mybench
```

Then confirm from outside:

```bash
curl -I https://example.com
# Look for: HTTP/2 200  and  strict-transport-security header
```

Or with openssl:

```bash
openssl s_client -connect example.com:443 -servername example.com < /dev/null 2>/dev/null \
  | openssl x509 -noout -dates -subject
```

---

## DNS-01 setup (Cloudflare)

### 1. Create a Cloudflare API Token

1. Go to <https://dash.cloudflare.com/profile/api-tokens>
2. Click **Create Token** → use the **Edit zone DNS** template
3. Set **Zone Resources** to the specific zone(s) you need
4. Copy the token; you will not see it again

!!! tip "API Token vs Global API Key"
    Always prefer the API Token. The Global API Key grants full account access and is a higher-risk credential. If you must use it, pass `--api-key` and `--email` instead of `--api-token`.

### 2. Save credentials

Global (applies to all benches unless overridden):

```bash
fm ssl dns-config cloudflare --api-token YOUR_TOKEN
```

Bench-specific override:

```bash
fm ssl dns-config cloudflare mybench --api-token DIFFERENT_TOKEN
```

Verify:

```bash
fm ssl dns-config cloudflare --show
```

### 3. Dry run

```bash
fm ssl add mybench example.com --challenge dns01 --dry-run
```

acme.sh adds a `_acme-challenge.example.com` TXT record, waits for propagation, validates, then removes it.

!!! info "DNS propagation"
    TXT record propagation typically takes 30 seconds to 5 minutes, but can take longer on some registrars. If the dry-run times out, retry or pass `--wait-for-dns` to let FM poll automatically (every 30 s, up to 5 minutes).

### 4. Issue the certificate

```bash
fm ssl add mybench example.com --challenge dns01
```

### 5. Wildcard certificates

DNS-01 is the only challenge type that supports wildcards. Issue a wildcard alongside the apex:

```bash
fm ssl add mybench "*.example.com" --challenge dns01
```

!!! note
    Many browsers require both the apex (`example.com`) and the wildcard (`*.example.com`) to be covered. Issue both, or use a SAN cert covering both names.

### 6. CNAME delegation

If your DNS zone is hosted elsewhere but you want to delegate just the `_acme-challenge` subdomain to Cloudflare, add a CNAME in your primary DNS:

```
_acme-challenge.example.com. CNAME _acme-challenge.example.com.cf-delegated.example.com.
```

Then pass the delegation target to FM:

```bash
fm ssl add mybench example.com --challenge dns01 --cname _acme-challenge.example.com.cf-delegated.example.com
```

---

## Standalone mode

For Docker projects that are not FM benches but are on the `fm-global-frontend-network`, FM's nginx-proxy can front them and handle their certificates.

```bash
fm ssl add example.com --standalone
```

Renew and list work the same way with `--standalone`:

```bash
fm ssl list --standalone
fm ssl list --all              # bench + external certificates together
fm ssl renew --standalone example.com
fm ssl renew --standalone --all
```

---

## Local development certificates (`--dev`) {#local-development-certificates-dev}

For local or air-gapped development, `--dev` skips Let's Encrypt entirely and issues a certificate from a locally-generated CA:

```bash
fm ssl add mybench mybench.local --dev
```

No internet, public DNS, or open ports are required. The CA lives under `~/frappe/services/nginx-proxy/ssl/dev/`, and FM installs it into your system trust store (macOS keychain, Linux CA store, Firefox/Chrome NSS databases where available) so browsers accept the certificate. Renewal (`fm ssl renew`) re-issues the leaf certificate from the same CA.

---

## Renewals

### Manual

```bash
# Renew all certs for one bench
fm ssl renew mybench

# Renew one specific domain
fm ssl renew mybench example.com

# Force renewal even if not yet due (< 30 days remaining)
fm ssl renew mybench example.com --force

# Renew across all benches
fm ssl renew --all
```

### Automatic (cron)

Let's Encrypt certificates are valid for **90 days**. FM renews when fewer than 30 days remain. Set up a daily cron job:

```bash
crontab -e
```

Add:

```
0 3 * * * fm ssl renew --all
```

This runs at 3 am every day. FM skips benches that don't need renewal, so running it daily is safe.

!!! warning "Certificate lifetime is shrinking"
    Under the CA/Browser Forum schedule adopted in 2025, maximum certificate validity drops to **200 days** for certificates issued from March 2026, **100 days** from March 2027, and **47 days** from March 2029. Automated renewal is no longer optional; manual renewal will become impractical. Set up the cron job now.

---

## Removing a certificate

```bash
fm ssl remove mybench example.com

# Skip confirmation prompt
fm ssl remove mybench example.com --yes
```

---

## Troubleshooting

### Quick diagnostic checklist

Run these in order before diving deeper:

```bash
# 1. Is the domain resolving to this server?
dig +short example.com

# 2. Is port 80 reachable? (HTTP-01 only)
curl -v http://example.com/.well-known/acme-challenge/test

# 3. Is the certificate valid and trusted?
openssl s_client -connect example.com:443 -servername example.com < /dev/null 2>/dev/null \
  | openssl x509 -noout -dates -issuer -subject

# 4. Does the cert chain look complete?
openssl s_client -connect example.com:443 -servername example.com < /dev/null 2>/dev/null \
  | grep -E "^(depth|verify)"

# 5. Are FM containers running?
fm list
```

---

### Error reference

#### HTTP-01 failures

| Symptom | Cause | Fix |
|---|---|---|
| `Connection refused` on port 80 | Port blocked by firewall | Open port 80 in your cloud provider's security group / `ufw` |
| `Timeout during connect` | Wrong IP in DNS A record | Update A record to point to this server; wait for propagation |
| `Invalid response from http://example.com/.well-known/acme-challenge/` | Another web server is answering port 80 | Stop or reconfigure the conflicting service; only FM's nginx-proxy should own port 80 |
| `Too many certificates` rate limit error | Exceeded 5 certs for the same name set this week | Wait until next week or use `--dry-run` during testing |

#### DNS-01 failures

| Symptom | Cause | Fix |
|---|---|---|
| `DNS record not found` | TXT record not propagated yet | Wait 1–5 minutes and retry; use `--wait-for-dns` |
| `Authentication error` | API token lacks DNS Edit permission | Recreate the token with **Zone → DNS → Edit** |
| `Invalid API Token` | Token was revoked or typo | Run `fm ssl dns-config cloudflare --show` to inspect; re-save if needed |
| Old TXT record conflict | Previous failed attempt left a stale record | Delete `_acme-challenge.example.com` TXT records from Cloudflare dashboard manually |

#### Certificate errors in browser

| Symptom | Cause | Fix |
|---|---|---|
| `NET::ERR_CERT_DATE_INVALID` | Certificate expired | Run `fm ssl renew mybench` |
| `NET::ERR_CERT_AUTHORITY_INVALID` | Self-signed or incomplete chain | FM uses Let's Encrypt; check that `fullchain.pem` is served, not just `cert.pem` |
| `NET::ERR_CERT_COMMON_NAME_INVALID` | Domain mismatch | Confirm the cert's SANs cover the domain you're accessing: `openssl x509 -noout -ext subjectAltName` |
| Clock skew warning | Server time is wrong | Run `timedatectl` or `date -u`; sync NTP: `sudo timedatectl set-ntp true` |

!!! warning "Incomplete chain: the silent failure"
    Chrome auto-fetches missing intermediate certificates via the AIA extension, so a site may appear valid in Chrome but fail in `curl`, Firefox, and API clients. Always make sure nginx is serving `fullchain.pem` (which includes the intermediates), not just `cert.pem`. FM handles this automatically, but if you have manually customised nginx config, verify with:

    ```bash
    openssl s_client -connect example.com:443 -servername example.com < /dev/null 2>/dev/null | grep -c "^---"
    # Should return 3 or more (root + intermediate + leaf)
    ```

#### Renewal failures

| Symptom | Fix |
|---|---|
| Cron ran but cert not renewed | Check cron logs: `grep CRON /var/log/syslog`; confirm FM is on `$PATH` in the cron environment |
| `force renewal failed` | Run `fm ssl acme-sh --info -d example.com` to inspect acme.sh state |
| DNS credentials expired | Re-save with `fm ssl dns-config cloudflare --api-token NEW_TOKEN` and run `fm ssl renew mybench --force` |

---

## Certificate file locations

All SSL files are stored in the global nginx-proxy service directory. Understanding this structure is helpful for debugging:

| Path | Purpose |
|------|---------|
| `~/frappe/services/nginx-proxy/ssl/acmesh/.acme.sh/` | acme.sh installation and internal certificate database |
| `~/frappe/services/nginx-proxy/ssl/acmesh/<domain>/fullchain.pem` | Full certificate chain (includes intermediates) |
| `~/frappe/services/nginx-proxy/ssl/acmesh/<domain>/key.pem` | Private key for the domain |
| `~/frappe/services/nginx-proxy/certs/<domain>.crt` | Symlink to fullchain.pem (nginx-proxy reads this) |
| `~/frappe/services/nginx-proxy/certs/<domain>.key` | Symlink to key.pem (nginx-proxy reads this) |
| `~/frappe/services/nginx-proxy/vhostd/<domain>` | HTTP→HTTPS redirect configuration |
| `~/frappe/services/nginx-proxy/confd/<domain>.conf` | Nginx server block (standalone mode only) |
| `~/frappe/services/nginx-proxy/external_domains.toml` | Registry of standalone domain certificates |

!!! info "Why symlinks?"
    FM uses symlinks so nginx-proxy can read certificates from a fixed location (`certs/`) while the actual certificate files are managed by acme.sh in its own directory structure (`ssl/acmesh/`).

---

## Advanced: raw acme.sh access

FM exposes acme.sh directly for edge cases. Use this only if the `fm ssl` commands don't cover your need.

```bash
# List all certs acme.sh knows about
fm ssl acme-sh --list

# Detailed info for one domain
fm ssl acme-sh --info -d example.com

# Check acme.sh version
fm ssl acme-sh --version

# Upgrade bundled acme.sh
fm ssl acme-sh --upgrade
```

!!! danger
    Commands run via `fm ssl acme-sh` bypass FM's certificate management. Certificates issued or removed this way will not be reflected in `fm ssl list` and FM will not manage their installation into bench nginx configs. Prefer `fm ssl add/remove/renew` for all normal workflows.

---

## Security notes

- **Credentials**: Cloudflare API credentials are stored globally in `~/frappe/fm_config.toml` (bench-specific overrides live in the bench's `bench_config.toml`); key reference: [DNS challenge providers](../reference/configuration.md#dns-providers). Restrict file permissions: `chmod 600 ~/frappe/fm_config.toml`. Remove saved credentials with `fm ssl dns-config cloudflare --remove`.
- **Token scope**: Use a per-zone API Token, not the Global API Key. If the token is compromised, you can revoke it without rotating your entire Cloudflare account.
- **Certs in version control**: Never commit `*.pem` or `*.key` files. Add them to `.gitignore`.
- **Staging vs production**: Use `--dry-run` (staging) during setup and testing. Production rate limits are shared across all users of a domain; hitting them blocks certificate issuance for everyone on that domain for up to a week.

---

!!! info "See also"
    - [fm ssl command reference](../commands/ssl.md): all flags and subcommands
    - [Cloudflare DNS Config](../commands/ssl-dns-config-cloudflare.md): detailed Cloudflare token setup
    - [Environments](environments.md): prod vs dev environment differences
    - [Configuration reference](../reference/configuration.md#ssl-certificates): how issued certificates are recorded in `bench_config.toml`
