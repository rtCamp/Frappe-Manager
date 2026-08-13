# Hosting on a Server

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
uv tool install --python 3.13 frappe-manager
```

See [Installation](../getting-started/installation.md) for pipx and other options.

## 3. Create the production bench

Name the bench after the domain it will serve; the bench name is the primary domain:

```bash
fm create clientone.example.com -e prod
```

`prod` gives you Gunicorn, restart-on-crash defaults, and no admin tools (the right defaults for a public server). See [Environments](environments.md) for exactly what changes.

## 4. Add HTTPS

```bash
fm ssl add clientone.example.com clientone.example.com --dry-run   # validate first
fm ssl add clientone.example.com clientone.example.com             # then issue
```

Set up automated renewals once per server:

```bash
# Add to crontab
0 3 * * * fm ssl renew --all
```

The [SSL guide](ssl.md) covers all of this in depth, including the DNS-01 (Cloudflare) challenge; use `--challenge dns01` when port 80 is blocked or you need wildcard certificates.

## 5. Verify

```bash
fm info clientone.example.com
curl -I https://clientone.example.com
```

Look for `HTTP/2 200` and the `strict-transport-security` header. `fm info` shows the environment, domains, and credentials.

## Adding more client benches

One machine runs many benches behind the same nginx proxy; each new client is a repeat of steps 3-5 with its own domain:

```bash
fm create clienttwo.example.com -e prod
fm ssl add clienttwo.example.com clienttwo.example.com
```

- Point the new domain's DNS record at the same server first.
- Domains must be unique across the machine: fm refuses to create a bench whose domain another bench already claims.
- Certificates are per-domain: run `fm ssl add` for each bench (and each [alias domain](domains.md#alias-domains)).

## Staying safe

- **Backups**: back up sites regularly (`fm shell <bench> -c "bench --site <bench> backup"`) and copy the backups off the server. Test restores on a non-production bench.
- **Upgrading fm**: keep the CLI and your benches in sync; see [Upgrading fm](../getting-started/installation.md#upgrading-fm) (`fm self update` then `fm migrate --all-benches`).
- **Monitoring**: report the web process to New Relic APM; see [Monitoring](environments.md#monitoring-new-relic).
- **Web concurrency**: Gunicorn worker and thread counts have sensible RAM/CPU-based defaults; see [Web Serving & Concurrency](../concepts/web-serving.md).
- **Background jobs**: queue and worker tuning; see [Background Jobs & Workers](../concepts/background-jobs.md).

## Prefer immutable releases?

This runbook keeps code editable on the server. For repeatable image-based deploys (bake once, roll out with zero downtime, roll back in one command), see [Deployment](../deploy/index.md).
