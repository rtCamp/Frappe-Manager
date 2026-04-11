# SSL / HTTPS

Use fm to obtain Let's Encrypt certificates via acme.sh. Use HTTP-01 when your domain points to the machine. Use DNS-01 with Cloudflare if you cannot use HTTP-01 or need wildcards.

Prerequisites

- Your domain must point at the server for HTTP-01.
- Ports 80 and 443 must be reachable for HTTP-01.

HTTP-01 (default)

```bash
fm ssl add mybench example.com
```

Dry run (test without contacting Let's Encrypt):

```bash
fm ssl add mybench example.com --dry-run
```

DNS-01 (Cloudflare)

First save Cloudflare credentials to the global config:

```bash
fm ssl dns-config cloudflare --api-token YOUR_TOKEN
```

Then issue the cert using DNS challenge:

```bash
fm ssl add mybench example.com --challenge dns01
```

Other useful commands:

```bash
fm ssl list mybench
fm ssl renew mybench
fm ssl renew --all
fm ssl remove mybench example.com
fm ssl acme-sh -- --list
```

Standalone mode (for non-FM Docker projects on the shared network):

```bash
fm ssl add --standalone example.com
```

!!! warning
    Do not use flags that mention an email address like `--letsencrypt-email`. The CLI does not accept that flag.

!!! tip
    To automate renewals run `fm ssl renew --all` from a cronjob or system scheduler.
