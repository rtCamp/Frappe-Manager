# SSL / HTTPS

Frappe Manager issues and installs Let's Encrypt certificates for your benches using either the HTTP-01 challenge (the default) or DNS-01 through Cloudflare. It can also mint locally-trusted certificates from its own CA for development, import a certificate you already have with `--custom`, serve origins behind an external TLS terminator such as Cloudflare with `--behind-proxy`, and front external Docker projects that share its proxy network.

## What `fm ssl add` does

With no mode flag, `fm ssl add` issues from Let's Encrypt:

1. Installs acme.sh on first use into `~/frappe/services/nginx-proxy/ssl/acmesh/.acme.sh/`.
2. Runs the ACME challenge against Let's Encrypt. For HTTP-01, acme.sh drops the validation file into the bench's nginx webroot, which is served at `/.well-known/acme-challenge/`. For DNS-01, acme.sh creates and then removes a `_acme-challenge` TXT record through the Cloudflare API.
3. Copies the issued certificate to `~/frappe/services/nginx-proxy/ssl/acmesh/<domain>/` and symlinks it into the proxy's `certs/` directory, where nginx-proxy reads it.
4. Writes a `vhostd/<domain>` snippet that redirects HTTP to HTTPS, then restarts nginx-proxy.
5. Records the certificate in the bench's `bench_config.toml`. When the domain is the site's own name, it also sets that site's `host_name` to `https://<domain>`; a certificate for an alias leaves `host_name` alone, because a site's name is its canonical domain and an alias is an alternate for it: Frappe builds emails, password resets and links from `host_name`, so writing the alias there would silently rename the site.

`--dev` ([below](#local-development-certificates-dev)) and `--custom` ([below](#custom-certificates)) replace the first three steps: `--dev` signs a certificate from fm's local CA, and `--custom` validates and copies files you supply, into `ssl/dev/<domain>/` and `ssl/custom/<domain>/` respectively. The symlink, redirect, restart and config steps are identical in every mode.

!!! warning "Nothing renews on its own"
    fm installs no cron job, timer, or daemon. `fm ssl renew` only runs when you run it. Set up the cron job in [Renewals](#renewals) the same day you issue your first certificate.

## Challenge types

| | HTTP-01 | DNS-01 |
|---|---|---|
| **How it works** | Let's Encrypt fetches a validation file from `http://yourdomain/.well-known/acme-challenge/` | acme.sh adds a TXT record to your Cloudflare zone, then removes it |
| **Port 80 required** | ✅ Yes | ❌ No |
| **Wildcard certs** | ❌ No | ✅ Yes |
| **Best for** | Public domains with open ports | Firewalled servers, wildcard domains, internal infrastructure |
| **Supported DNS providers** | N/A | Cloudflare |
| **Setup complexity** | Simple (default) | Requires API credentials |

Neither applies to local development: `--dev` issues a locally-trusted certificate from fm's own CA, with no internet, public DNS, or open port (see [below](#local-development-certificates-dev)).

!!! note "HTTP-01 and a password prompt coexist; maintenance mode does not"
    `fm auth` puts an HTTP basic auth prompt in front of a bench, but the bench nginx serves `/.well-known/acme-challenge/` with `auth_basic off`, so issuance and renewal are never blocked by it. `fm maintenance` is different: it exempts nothing by default, so if a renewal falls due while the maintenance page is up, allow the path explicitly with `fm maintenance <bench> --allow-path '/.well-known/acme-challenge/*'`.

---

## Before you start

**Both challenge types:** the domain must already be configured on the bench. `fm ssl add` only accepts the bench's own name or one of its alias domains, and refuses anything else. Add an alias first:

```bash
fm update mybench --add-alias example.com
```

### HTTP-01 checklist

- [ ] The domain's A record points to this server's public IP
- [ ] Ports 80 and 443 are open in your firewall and security group
- [ ] No other process owns port 80 on the host

### DNS-01 checklist

- [ ] Cloudflare manages the DNS zone for the domain
- [ ] You have a Cloudflare API Token scoped to **Zone > DNS > Edit** for that zone
- [ ] The token is saved with `fm ssl dns-config cloudflare`

!!! warning "Rehearse first"
    `--dry-run` issues against the Let's Encrypt **staging** CA and keeps nothing: no certificate is installed, no symlink or `vhostd` redirect is written, and nginx is not restarted. It does still reach the staging endpoint and register an account there, so it is a network operation, not an offline check. Use it to burn mistakes on staging instead of your [production rate limit](https://letsencrypt.org/docs/rate-limits/) (50 certificates per registered domain per week, 5 per identical set of names per week).

---

## HTTP-01 setup

```bash
fm ssl add mybench/example.com --dry-run
```

A successful rehearsal ends with `Certificate generated successfully (staging)` followed by the skipped-step lines (symlinks, redirect config, nginx restart, config save). Then issue for real:

```bash
fm ssl add mybench/example.com
```

Verify:

```bash
fm ssl list mybench
curl -I https://example.com
```

`fm ssl list mybench` also shows configured domains that have no certificate yet, which is the fastest way to spot an alias you forgot to secure.

---

## DNS-01 setup (Cloudflare)

### 1. Create a Cloudflare API Token {#dns-01-cloudflare-api-token}

1. Go to <https://dash.cloudflare.com/profile/api-tokens>
2. **Create Token**, then use the **Edit zone DNS** template, which grants exactly **Zone > DNS > Edit**
3. Restrict **Zone Resources** to the zones you issue for, and copy the token before leaving the page

!!! tip "API Token, not Global API Key"
    The Global API Key grants full account access and cannot be scoped. fm accepts it (`--api-key` together with `--email`) but the token is the safer credential and needs no email.

### 2. Save credentials

```bash
# Global default: used by every bench that stores nothing of its own
fm ssl dns-config cloudflare --api-token YOUR_TOKEN

# Per-bench default, which wins over the global one
fm ssl dns-config cloudflare mybench --api-token DIFFERENT_TOKEN

# A second Cloudflare account, stored under a label
fm ssl dns-config cloudflare --name client-zones --api-token OTHER_TOKEN

# Inspect what is stored (secrets masked, writes nothing)
fm ssl dns-config cloudflare --show
```

Without `--name`, the credentials land under the label `cloudflare`: in `[ssl.dns_providers.cloudflare]` of `~/frappe/fm_config.toml` globally, or of that bench's `bench_config.toml` when you pass a bench name, and the bench entry wins over the global one. That label is the default: it is what a certificate uses when it names none.

With `--name`, the credentials land in `[ssl.dns_providers.<label>]` instead, at the global scope or the bench scope depending on whether you passed a bench name, and only a certificate that names that label uses them. Both scopes take the identical table, so a label means the same thing in either file. Labels are how one bench holds certificates for two separate Cloudflare accounts, or for two least-privilege tokens of the same account. Full field list and resolution order: [DNS providers](../reference/configuration.md#dns-providers).

### 3. Issue

```bash
fm ssl add mybench/example.com --challenge dns01 --dry-run
fm ssl add mybench/example.com --challenge dns01

# Authenticate this domain against a labelled credential set
fm ssl add mybench/client.example.com --challenge dns01 --dns-provider client-zones
```

`--dns-provider` records the label on the certificate, so every later renewal reaches for the same account. Omit it and the certificate uses the default pair described above. The flag applies to DNS-01 only, and `fm ssl add` resolves the label before it changes anything, so a typo is refused on the spot instead of halfway through issuance. A label stored at neither scope is an error at renewal too: fm refuses rather than authenticating against whichever other account happens to be configured.

To see what a domain will actually use, `fm ssl list mybench` has a **DNS Provider** column: `default` for the unlabelled account, the label for a certificate that names one, and `missing` when the label resolves to nothing at either scope.

acme.sh adds `_acme-challenge.example.com`, waits for propagation on its own, validates, then deletes the record. Propagation is usually well under a minute on Cloudflare; if a run fails on a missing TXT record, retry.

### 4. Wildcard certificates

DNS-01 is the only challenge type that can cover a wildcard. The wildcard has to be a configured domain of the bench first:

```bash
fm update mybench --add-alias '*.example.com'
fm ssl add 'mybench/*.example.com' --challenge dns01
```

!!! note
    A wildcard certificate does not cover the apex. If you serve both `example.com` and `www.example.com`, issue a certificate for each name.

### 5. CNAME delegation

If your zone lives outside Cloudflare, delegate only the challenge record. `--cname` takes the **delegated zone**, not the full record name: fm passes it to acme.sh as `--challenge-alias`, and acme.sh writes into `_acme-challenge.<delegated zone>`.

Add this to your primary DNS:

```
_acme-challenge.example.com. CNAME _acme-challenge.acme.example.net.
```

Then issue against the delegated zone, whose credentials are the ones that must be saved with `fm ssl dns-config`:

```bash
fm ssl add mybench/example.com --challenge dns01 --cname acme.example.net
```

---

## Standalone mode

`--standalone` secures a domain served by an external Docker project rather than a bench. fm writes a server block into `~/frappe/services/nginx-proxy/confd/<domain>.conf` so the ACME challenge can be answered and TLS terminated even before a backend exists, and records the domain in `external_domains.toml`.

```bash
fm ssl add example.com --standalone
```

`list`, `renew` and `remove` take `--standalone` the same way, and `fm ssl list all` covers external domains and every bench at once.

Connect the backend by giving its container the nginx-proxy environment variables and joining fm's proxy network:

```yaml
services:
  your-app:
    environment:
      VIRTUAL_HOST: example.com
      VIRTUAL_PORT: 80
    networks:
      - fm-global-frontend-network

networks:
  fm-global-frontend-network:
    external: true
```

Until that is in place the domain answers `503 Backend Not Connected` over HTTPS, which means the certificate is fine and only the backend is missing.

`--skip-dns-check` and `--wait-for-dns` apply to standalone mode only; bench mode ignores them. `--skip-dns-check` turns off the pre-flight lookup (the A record for HTTP-01, the delegation CNAME when `--cname` is given), and `--wait-for-dns` polls for the delegation CNAME for up to 5 minutes, so it is only meaningful together with `--cname`.

---

## Bring your own certificate (`--custom`) {#custom-certificates}

```bash
fm ssl add mybench/example.com --custom --cert ./example.com.crt --key ./example.com.key
```

`--custom` imports a certificate you already have instead of issuing one: no ACME account, no challenge, no call to Let's Encrypt. fm validates the files, copies their bytes into `~/frappe/services/nginx-proxy/ssl/custom/<domain>/`, symlinks them into the proxy's `certs/` directory, writes the `vhostd/<domain>` HTTP to HTTPS redirect, records `ssl_type = "custom"` in the bench's `bench_config.toml`, and updates the site's `host_name` under the same rule as an issued certificate: `https://<domain>` when the domain is the site's own name, untouched when it is an alias.

Give `--cert` the full chain (leaf first, then intermediates) in PEM: fm copies the bytes verbatim, and nginx serves exactly what you supply. `--key` must be the matching private key, also PEM and unencrypted; decrypt a password-protected key first.

The import is refused when a file is missing or unreadable, when it does not parse as PEM, when the key does not match the certificate's public key, when the certificate does not cover the domain (SAN is checked first, then CN as the pre-SAN fallback), or when the certificate has already expired. A certificate with fewer than 30 days left imports with a warning, because fm cannot renew it for you.

`--custom` is bench mode only, like `--dev`, and the domain must already be configured on the bench (see [Before you start](#before-you-start)). It cannot be combined with `--dev`, `--standalone`, `--dry-run`, `--cname`, `--dns-provider`, or an explicitly passed `--challenge`, and `--cert`/`--key`/`--ca` are refused without `--custom`.

!!! warning "fm will not rotate this certificate"
    Renewal stays your job. `fm ssl renew` reports a custom certificate and changes nothing, at any age: fm keeps only the imported bytes, not the paths to your original files, and it never re-issues a certificate it did not create. When you have a replacement, run `fm ssl add BENCH/DOMAIN --custom` again with the new files. Expiry shows up in `fm ssl list` like any other certificate, so put the date in whatever calendar your issuer's renewals already live in.

### Private CAs (`--ca`)

If the certificate chains to a private CA rather than a public root, pass the CA bundle with `--ca`. fm stores it beside the certificate as `ca.pem` and mounts it into the bench's frappe, socketio, schedule and worker containers, with `NODE_EXTRA_CA_CERTS` and `REQUESTS_CA_BUNDLE` pointing at it, so server-side HTTPS calls to the bench's own domain (PDF rendering, OAuth callbacks, `get_url` fetches) trust it. This is the same mechanism `--dev` already uses for fm's local CA, and a bench holding both trusts a combined bundle. The import rewrites the bench's compose files on the spot and tells you to run `fm start BENCH`, which recreates only the services whose definition changed; the trust is live from that point, and a batch of imports needs just one `fm start` at the end. A publicly-trusted certificate needs no `--ca` at all.

---

## Local development certificates (`--dev`) {#local-development-certificates-dev}

```bash
fm ssl add mybench/mybench.local --dev
```

`--dev` skips Let's Encrypt entirely: no internet, public DNS, or open ports. On first use fm generates a CA under `~/frappe/services/nginx-proxy/ssl/dev/ca/` and installs it into the host trust store (macOS login keychain, the Linux system CA store, plus Firefox and Chrome NSS databases when `certutil` is present) so browsers accept the leaf certificate.

Leaf certificates are valid for 397 days, so they never fall inside the 30-day renewal window during normal use. To re-issue one from the same CA, force it:

```bash
fm ssl renew mybench/mybench.local --force
```

`--dev` is bench mode only and cannot be combined with `--standalone`.

---

## Behind an external TLS terminator (`--behind-proxy`) {#behind-proxy}

```bash
fm ssl add mybench/example.com --challenge dns01 --behind-proxy
```

`--behind-proxy` (alias `--edge-tls`) is for an origin behind an external TLS terminator: the browser reaches the edge over HTTPS and the edge forwards to your server, Cloudflare's proxied (orange-cloud) mode being the canonical case. Without the flag the origin's HTTP to HTTPS redirect keys on its own connection scheme, which behind a Flexible-style edge is always http, so every request answers 301 and the browser reports a redirect loop.

It is a modifier on the certificate methods above, not a method of its own. It does four things:

1. Records `behind_proxy = true` on that domain's `[[ssl.certificates]]` entry.
2. Still issues or imports a certificate by whichever method you paired it with.
3. Switches the domain's HTTP to HTTPS redirect to key on the forwarded proto instead of the connection scheme. The value falls back to the connection's own scheme when no header arrived at all, which is why a visitor who reaches the origin directly over plain HTTP is still upgraded rather than served http forever.
4. Makes this bench's web server trust `X-Forwarded-Proto` (gunicorn, so on a prod bench: see [When it takes effect](#when-it-takes-effect)). `host_name` is already https whenever the certified domain is the site's own name (an alias certificate leaves it alone), and Frappe builds emails, password resets and links from it, so those were never broken; what the trust fixes is request-scoped: the post-login redirect, the session cookie's Secure flag, and the endpoints OAuth advertises. It is deliberately per bench and never a global default, because the proxy passes a client-supplied `X-Forwarded-Proto` straight through, so trusting it everywhere would let any anonymous client dictate the scheme Frappe believes it is serving.

Bare `--behind-proxy` is refused: pass `--dev`, `--custom`, or an explicit `--challenge`. The mode exists so the origin's own internal HTTPS self-calls (PDF rendering, OAuth callbacks, `get_url` fetches) keep working, and those only work if the origin actually holds a trusted certificate on its own port 443. A behind-proxy mode with no certificate would be the one configuration that cannot deliver what the mode is for.

!!! note "The whole bench must agree"
    The redirect is per domain, but the forwarded-proto trust is per bench: one web server process serves every site the bench has. Bench nginx relays the header rather than recomputing it, and the global proxy passes a client-supplied value through, so if one domain opted in, an anonymous client could forge the header for every other domain the bench serves, stripping the Secure flag from a direct domain's session cookie. fm therefore refuses to mix: adding a `--behind-proxy` certificate to a bench holding an ordinary one is an error, and so is the reverse, with the message "gunicorn cannot trust the header for one domain and not another. Match the bench's existing setting, or use a separate bench." Those are also your two options.

### Pairing it with a certificate method

| Pairing | Origin certificate | Cloudflare SSL/TLS mode | Verdict |
|---|---|---|---|
| `--challenge dns01` | Real Let's Encrypt; renews with no inbound connection | **Full (strict)** | **Recommended** |
| `--custom` with a Cloudflare Origin CA certificate | 15-year certificate Cloudflare mints for your origin; pass its root with `--ca` so self-calls trust it (Origin CA is not publicly trusted) | **Full (strict)** | Recommended alternative |
| `--dev` | fm's local CA | Full (the edge cannot strictly verify a private CA) | Development and internal use |
| `--challenge http01` | Real Let's Encrypt | Full (strict) | Permitted, fragile: see warning |

Flexible mode at the edge "works" with any pairing, but it downgrades edge-to-origin traffic to plain HTTP, defeating half the point once the origin holds a certificate. Move the edge to Full (strict) as soon as the fm side is done.

A `--dev` certificate and a dev-environment bench are different axes: the table above is about the certificate method. On a dev-environment bench the trust half of the mode has nothing to attach to, whatever the method; see [When it takes effect](#when-it-takes-effect).

!!! warning "HTTP-01 behind an edge is fragile"
    Issuance works today and breaks silently later. Every renewal needs Let's Encrypt to fetch `/.well-known/acme-challenge/` over plain HTTP through the edge, so port 80 must stay reachable end to end and no edge rule (Always Use HTTPS, a redirect rule, WAF, cache) may ever touch that path. A rule someone adds at the edge months from now fails the next renewal, not the current certificate, so the damage surfaces as an expiry. Prefer `--challenge dns01`, which renews with no inbound connection at all.

### When it takes effect

The redirect switches in the same command, in every environment: the vhost snippet is rewritten and the proxy restarted as part of the add, and the redirect runs at the global proxy regardless of which web server the bench runs.

The forwarded-proto trust depends on the bench's environment, because it attaches to gunicorn and only a prod bench runs gunicorn:

- **Prod bench.** The trust is rewritten into the bench's gunicorn launch script in the same command, but a running gunicorn keeps its old script until its supervisor process is restarted, so fm prints two instructions: "Run 'fm start BENCH' to apply it" for the compose half, and "Run 'fm restart BENCH' to apply the forwarded-proto trust to gunicorn (the compose converge above does not reach it)." One `fm restart` at the end of a batch of adds is enough. The launch script resolves the one trusted address (this bench's own nginx, via its site-scoped alias) each time gunicorn starts, and if that resolution ever fails it trusts nothing rather than everything.
- **Dev bench.** A dev-environment bench serves through `bench serve`, not gunicorn, so there is nothing for the trust to apply to. The mode is still allowed, because the redirect half is genuinely useful there, and fm says so instead of printing a restart that would do nothing: "'BENCH' is a dev-environment bench (runs 'bench serve', not gunicorn), so that trust has nothing to apply to yet. It takes effect if the bench is later switched to prod ('fm update BENCH --environment prod')."

Removal mirrors this, and it is the security-relevant half: when `fm ssl remove` takes away the LAST behind-proxy certificate a bench holds, fm rewrites the launch script without the trust and, on a prod bench, prints "Run 'fm restart BENCH' to drop the forwarded-proto trust from gunicorn", so the bench does not keep trusting a forwarded proto no certificate is asking for. On a dev bench it notes instead that the trust being removed "was never active". While other behind-proxy certificates remain, the trust stays, because they still need it.

### The Cloudflare hint

fm checks where the domain resolves when you run `fm ssl add`. A domain inside Cloudflare's published ranges without `--behind-proxy` gets a hint that the flag may be wanted; `--behind-proxy` on a domain that does not currently resolve into a known CDN range gets a note that it may be unnecessary. Both are advisory: nothing blocks, and when DNS cannot answer within a few seconds fm says nothing rather than guessing.

`--behind-proxy` handles the scheme. Real client IPs are the other half of sitting behind an edge: run `fm self real-ip` so logs, `fm auth --allow-ip` and frappe's rate limiting see the visitor rather than the edge ([Hosting guide](hosting.md)).

---

## Renewals {#renewals}

Let's Encrypt certificates are valid for 90 days. fm treats a certificate as due when fewer than 30 days remain; one that is not due is reported and left alone unless you pass `--force`.

A custom certificate is never renewed: `fm ssl renew` answers with a reminder to re-import and changes nothing, whatever its age (see [Bring your own certificate](#custom-certificates)).

```bash
fm ssl renew mybench                        # every certificate on one bench
fm ssl renew all                            # every bench
```

Renewal resolves a DNS-01 certificate's credentials afresh every time, from the `dns_provider` label recorded on the certificate, so rotating a Cloudflare token means re-saving it under the same label and nothing else.

Because fm ships no scheduler, add the cron job yourself:

```bash
crontab -e
```

```
0 3 * * * fm ssl renew all
```

Running it daily is safe: certificates that are not due are skipped. Certificate lifetimes are also shrinking under the CA/Browser Forum schedule adopted in 2025 (200 days from March 2026, 100 from March 2027, 47 from March 2029), so manual renewal will stop being practical.

!!! tip "Check the cron environment"
    Cron runs with a minimal `PATH`. Use the absolute path to `fm` in the crontab entry, or set `PATH` at the top of the crontab, otherwise the job fails silently every night.

---

## Troubleshooting

Start here:

```bash
# Does the domain resolve to this server?
dig +short example.com

# Is port 80 reachable from outside? (HTTP-01)
curl -v http://example.com/.well-known/acme-challenge/test

# What certificate is actually being served, and does it verify?
openssl s_client -connect example.com:443 -servername example.com < /dev/null 2>&1 \
  | grep -E "^(depth|verify|subject|issuer)"

# Are the containers up?
fm list
```

### HTTP-01 failures

| Symptom | Cause | Fix |
|---|---|---|
| `Domain 'example.com' is not configured for bench 'mybench'` | The domain is neither the bench name nor an alias | `fm update mybench --add-alias example.com`, then retry |
| Connection refused on port 80 | Firewall or security group | Open port 80 to the internet |
| Connect timeout | A record points elsewhere | Correct the A record and wait for the TTL to expire |
| Let's Encrypt gets an unexpected response from `/.well-known/acme-challenge/` | Another web server is answering port 80 | Stop or move it; only fm's nginx-proxy should own port 80 |
| Rate limit refusal | More than 5 certificates for the same name set this week | Wait, and use `--dry-run` while experimenting |

### DNS-01 failures

| Symptom | Cause | Fix |
|---|---|---|
| acme.sh reports the TXT record is missing | The Cloudflare API call did not take effect, or a stale record from a failed run is still in the zone | Delete leftover `_acme-challenge.example.com` TXT records in the Cloudflare dashboard, then retry |
| Cloudflare rejects the request | Token lacks **Zone > DNS > Edit**, or was scoped to a different zone | Recreate the token from the **Edit zone DNS** template and re-save it |
| `Failed to get DNS credentials` | No credentials stored for this bench or globally | `fm ssl dns-config cloudflare --show` to confirm, then re-save |
| Delegation validation fails with `--cname` | The `_acme-challenge` CNAME is missing or points at the wrong target | Expected target is `_acme-challenge.<value of --cname>`; fix the record, or poll with `--wait-for-dns` in standalone mode |
| `DNS provider 'acct-b' is not configured` | The certificate's `dns_provider` label is stored at neither bench nor global scope | `fm ssl dns-config cloudflare --show` lists every label it can see; store the missing one with `--name acct-b`, or re-issue the certificate against a label that exists |

### Certificate errors in the browser

| Symptom | Cause | Fix |
|---|---|---|
| `NET::ERR_CERT_DATE_INVALID` | Expired, because nothing renewed it | `fm ssl renew mybench`, then fix the cron job |
| `NET::ERR_CERT_COMMON_NAME_INVALID` | The name you visited is not in the certificate | `fm ssl list mybench` to see which domains are covered; a wildcard does not cover the apex |
| A `--dev` certificate is untrusted in Firefox or Chrome, but fine in `curl` | The CA reached the system store but `certutil` was missing, so the NSS databases were skipped | Install `libnss3-tools` (Debian/Ubuntu) or `nss-tools` (Fedora), delete `~/frappe/services/nginx-proxy/ssl/dev/ca/.installed`, then re-run `fm ssl add ... --dev` |
| Clock skew warning | Host clock is wrong | `sudo timedatectl set-ntp true` |
| `ERR_TOO_MANY_REDIRECTS` behind Cloudflare | The domain is proxied (orange-clouded) but the certificate was added without `--behind-proxy`, so the origin's redirect keys on a connection scheme that is always http | Re-add the certificate with `--behind-proxy` ([details](#behind-proxy)) |

### Renewal failures

| Symptom | Fix |
|---|---|
| Cron ran but nothing renewed | Confirm `fm` resolves in the cron environment; run the same command by hand to see the real error |
| A renewal fails where issuance worked | Inspect acme.sh's own view of the certificate: `fm ssl acme-sh --info -d example.com` |
| Cloudflare credentials were rotated | Re-save with `fm ssl dns-config cloudflare --api-token NEW_TOKEN`, then `fm ssl renew mybench --force` |

---

## Certificate file locations

Everything lives under the global nginx-proxy service directory:

| Path | Purpose |
|------|---------|
| `~/frappe/services/nginx-proxy/ssl/acmesh/.acme.sh/` | acme.sh installation and its own certificate database |
| `~/frappe/services/nginx-proxy/ssl/acmesh/<domain>/fullchain.pem` | Certificate chain fm installs |
| `~/frappe/services/nginx-proxy/ssl/acmesh/<domain>/key.pem` | Private key |
| `~/frappe/services/nginx-proxy/ssl/dev/ca/` | Local dev CA (`rootCA.pem`, `rootCA-key.pem`, `.installed` sentinel) |
| `~/frappe/services/nginx-proxy/ssl/dev/<domain>/` | Dev leaf certificate and key |
| `~/frappe/services/nginx-proxy/ssl/custom/<domain>/` | Imported custom certificate: `key.pem`, `fullchain.pem`, and `ca.pem` when `--ca` was given |
| `~/frappe/services/nginx-proxy/certs/<domain>.crt` | Symlink nginx-proxy reads for the chain |
| `~/frappe/services/nginx-proxy/certs/<domain>.key` | Symlink nginx-proxy reads for the key |
| `~/frappe/services/nginx-proxy/vhostd/<domain>` | HTTP to HTTPS redirect snippet |
| `~/frappe/services/nginx-proxy/confd/<domain>.conf` | Server block for a standalone domain |
| `~/frappe/services/nginx-proxy/external_domains.toml` | Registry of standalone domains |

---

## Security notes

- **Credentials.** Cloudflare credentials sit in plain text under `[ssl.dns_providers]`: globally in `~/frappe/fm_config.toml`, per bench in that bench's `bench_config.toml`. Restrict permissions (`chmod 600 ~/frappe/fm_config.toml`) and delete a set with `fm ssl dns-config cloudflare --remove` when it is no longer needed. Name it with `--remove --name LABEL` whenever the scope holds more than one set; a bare `--remove` removes the only set stored there and otherwise refuses rather than guessing which you meant. Key reference: [DNS providers](../reference/configuration.md#dns-providers).
- **Token scope.** A per-zone API Token can be revoked in isolation; the Global API Key cannot.

---

!!! info "See also"
    - [fm ssl command reference](../commands/ssl.md): every flag and subcommand
    - [Cloudflare DNS config reference](../commands/ssl-dns-config-cloudflare.md)
    - [Environments](environments.md): prod and dev differences
    - [Configuration reference](../reference/configuration.md#ssl-certificates): how certificates are recorded in `bench_config.toml`
