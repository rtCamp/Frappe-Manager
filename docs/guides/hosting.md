# Hosting on a server

The end-to-end runbook: from a fresh Ubuntu server to production benches served over HTTPS, one domain per client. Every step links to the guide that covers it in depth.

This runbook uses `mount` benches in the `prod` environment: editable code under Gunicorn, the simple VPS setup. If you want immutable releases with rolling swaps and one-command rollbacks instead, see [Deployment](../deploy/index.md).

## 1. Prepare the server

On a fresh Ubuntu server:

- [ ] **Install Docker Engine**: follow [Docker's official instructions](https://docs.docker.com/engine/install/ubuntu/).
- [ ] **Let your user run Docker without root**: `sudo usermod -aG docker $USER`, then log out and back in. Verify with `docker ps`.
- [ ] **Never run fm with sudo**: fm refuses to run as root and exits without doing anything. Frappe's own bench refuses root too, and fm's service containers are shared per host rather than per user, so a root run would fight the benches your own user owns. Adding your user to the `docker` group above is what removes the temptation.
- [ ] **Open ports 80 and 443** in your firewall or cloud security group; the global nginx proxy listens on them, and Let's Encrypt HTTP-01 validation needs port 80.
- [ ] **Point DNS at the server**: create an `A` (or `AAAA`) record for each client domain, e.g. `clientone.example.com -> your.server.ip`. See [Domains](domains.md).

## 2. Install fm

```bash
uv tool install --python 3.14 frappe-manager
```

See [Installation](../getting-started/installation.md) for pipx and other options.

## 3. Create the production bench

Name the bench after the domain it will serve; the bench name is the primary domain:

```bash
fm create clientone.example.com -e prod
```

`prod` gives you Gunicorn, restart-on-crash defaults, and no admin tools (the right defaults for a public server). See [Environments](../concepts/environments.md) for exactly what changes.

## 4. Add HTTPS

```bash
fm ssl add clientone.example.com/clientone.example.com --test-ca   # validate first
fm ssl add clientone.example.com/clientone.example.com             # then issue
```

fm installs no renewal timer, so add the renewal once per server:

```bash
# crontab: safe to run daily, certificates that are not due are skipped
0 3 * * * fm ssl renew all
```

The [HTTPS certificates](ssl.md) covers the rest, including the DNS-01 (Cloudflare) challenge for when port 80 is blocked or you need a wildcard certificate (`--challenge dns01`).

## 5. Verify

```bash
fm info clientone.example.com
curl -I https://clientone.example.com
```

A `200` over HTTPS means DNS, the proxy, the certificate and the bench are all in place. `fm info` shows the environment, the domains, the credentials and the live state of every service.

## Adding more client benches

One machine runs many benches behind the same nginx proxy; each new client is a repeat of steps 3-5 with its own domain:

```bash
fm create clienttwo.example.com -e prod
fm ssl add clienttwo.example.com/clienttwo.example.com
```

- Point the new domain's DNS record at the same server first.
- Domains must be unique across the machine: fm refuses to create a bench whose domain another bench already claims.
- Certificates are per-domain: run `fm ssl add` for each bench (and each [alias domain](domains.md#alias-domains)).

## Staying safe

- **Backups**: fm does not back up site data; `bench backup` does, and the artefacts live inside the bench you are backing up. See [Backup and restore](backup-restore.md), then get the files off the server.
- **Upgrading fm**: keep the CLI and your benches in sync; see [Upgrading fm](../getting-started/installation.md#upgrading-fm) (`fm self upgrade` then `fm migrate all`).
- **Behind a CDN or load balancer**: trusting the front is one host-level setting, not a per-certificate flag. See [Running fm behind something else](#running-fm-behind-something-else) below.
- **Monitoring**: report the web process to New Relic APM; see [Monitoring](../concepts/environments.md#monitoring-new-relic).
- **Web concurrency**: Gunicorn worker and thread counts have sensible RAM/CPU-based defaults; see [Web serving and concurrency](../concepts/web-serving.md).
- **Background jobs**: queue and worker tuning; see [Background jobs and workers](../concepts/background-jobs.md).

## Running fm behind something else

Three shapes fm supports on one host:

| | Nothing in front | A local front (nginx, Caddy, another reverse proxy on this machine) | An edge or CDN (Cloudflare, a cloud load balancer) |
|---|---|---|---|
| Terminates TLS | fm | the front | the edge |
| fm receives | the real connection | plain HTTP + `X-Forwarded-Proto` | plain HTTP + `X-Forwarded-Proto` |
| Trust | nothing | `--local` | the edge's published ranges |

Two commands describe a fronted host, and neither is a per-certificate flag. The common case, a front on the same machine:

```bash
# Move the proxy off 80/443 so the local front can have them, and keep fm
# reachable from nowhere but that front
fm services ports --http 8080 --https 8443 --bind 127.0.0.1

# Tell fm which peer is allowed to speak for the client
fm services trusted-proxies set --local
```

`--local` is not a shorthand for `--trust 127.0.0.1`, and that address will not work: fm's proxy runs in a container, and docker rewrites the source of any connection originating on this machine to the bridge's gateway. A front on the same host therefore never reaches fm as loopback, whatever address it dialled. `--local` resolves the address fm actually sees; `--trust` refuses a loopback range rather than writing a set that silently matches nothing.

While a trusted set is configured, fm's redirects carry no port. The front owns the public one and fm cannot discover it, so a browser sent to HTTPS keeps the port it was already using, which is the front's. With nothing trusted fm is the public endpoint and its own published port is what the redirect names.

For an edge that owns its own IP ranges instead of running on this machine, fm keeps listening on 80/443 and the trusted set names the edge:

```bash
fm services trusted-proxies set --cdn cloudflare
```

### What the trusted set drives

One command, three consequences, because they answer the same question: did this request arrive through the front?

- The client IP fm, frappe's rate limiting and `fm auth enable --allow-ip` see.
- The scheme fm's own HTTP to HTTPS redirect believes. A peer outside the trusted set is judged on its actual connection, so a forged `X-Forwarded-Proto` from an untrusted address changes nothing, and the redirect loop a Flexible-style edge (always plain HTTP to the origin) would otherwise cause never happens.
- Whether gunicorn trusts the forwarded scheme at all, which is what the post-login redirect, the session cookie's Secure flag, and the endpoints OAuth advertises depend on.

`fm services trusted-proxies clear` turns off all three at once. Run it whenever the front is removed: leaving a stale trusted set behind means anyone who can now reach fm directly can claim to be any client, over any scheme, and fm believes them.

The first two take effect immediately. The third does not: gunicorn reads its trust from a wrapper script re-executed only by `fm restart <bench>`, which is why both `set` and `clear` end by naming the benches to restart. Until you run it a bench keeps the trust it had, and after a `clear` that means it is still believing a forwarded scheme nothing is vouching for.

### What fm cannot do for you

Trusting the front makes fm's own redirect unforgeable, but the proxy still relays whatever `X-Forwarded-Proto` a request carries on to gunicorn, for every request, not only the ones that came through the front. fm cannot rewrite that header itself: overriding it in one nginx location replaces the base image's whole header set there instead of adding to it, and re-declaring that set by hand is a fork that drifts on every image update. So a request that reaches fm directly, bypassing the front, still carries whatever scheme it claims, and gunicorn believes it.

Closing that gap is the operator's job: make fm unreachable except through the front. `--bind 127.0.0.1` on `fm services ports` is fm's own lever on a single-machine deployment like the example above, and a network firewall rule does the same for a front on another host. Neither is optional once a trusted set is configured: without one of them, the forged-header exposure this section opened with is still live.

Be precise about what `--bind` buys, because it is not everything. It takes fm off every interface but loopback, so nothing off this machine can reach it at all. It cannot tell the front apart from anything else running on the same host, since docker presents both as the bridge gateway (see `--local` above). A local process can therefore still reach fm and claim a scheme. That is the residual, and the answer to it is to not run untrusted code next to your origin, not a setting.

!!! warning "Moving off port 80 breaks Let's Encrypt HTTP-01"
    The CA connects to port 80 at the domain's public name to validate it, and nothing on this host can change that. `fm ssl add --challenge http01` is refused once `fm services ports` has moved off 80, naming the alternatives: `--challenge dns01`, `--dev`, or `--custom`.

A fronted bench still needs its own certificate for its self-calls: see [Behind an external TLS terminator](ssl.md#behind-proxy) in the HTTPS guide.

## Prefer immutable releases?

This runbook keeps code editable on the server. For repeatable image-based deploys (bake once, roll out with zero downtime, roll back in one command), see [Deployment](../deploy/index.md).
