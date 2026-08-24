# Domains & Remote Access

How requests reach a bench, how to serve it on more than one domain, and how to expose a local bench to the internet.

## How routing works

Every bench registers its domains with the shared **nginx-proxy** (one per machine, listening on ports 80/443). The proxy routes each request by its `Host:` header: no per-bench ports, no manual proxy config. See [Architecture](../reference/architecture.md) for the full topology.

`*.localhost` names resolve to `127.0.0.1` automatically on modern systems, so a bench named `mybench.localhost` works in the browser with zero DNS setup.

```bash
fm create mybench            # http://mybench.localhost just works
fm create shop.example.com   # any domain works; DNS is your job (see below)
```

Once a domain has a certificate it is served over both HTTP and HTTPS: fm configures the proxy not to redirect, so a plain-HTTP request keeps working rather than becoming a 301.

## Alias domains

A bench can serve additional domains alongside its primary name:

```bash
# at create time
fm create mybench --alias-domains www.example.com,api.example.com

# on an existing bench
fm update mybench --add-alias shop.example.com
fm update mybench --remove-alias shop.example.com
```

- Aliases are registered with the proxy and land on the same site.
- fm validates that no other bench on the machine claims the same domain (`--allow-domain-conflicts` skips the check; not recommended).
- HTTPS for aliases: certificates are per-domain; see the [SSL guide](ssl.md).
- Aliases are stored as [`alias_domains`](../reference/configuration.md#alias-domains) in `bench_config.toml`.

## Making a domain resolve

| Where the bench runs | What you need |
|---|---|
| your machine, `*.localhost` name | nothing (resolves automatically) |
| your machine, real domain name | an `/etc/hosts` entry: `127.0.0.1 shop.example.com` |
| a server | real DNS: point an `A`/`AAAA` (or `CNAME`) record at the server, then `fm ssl add` for HTTPS |

## Exposing a local bench to the internet

For webhooks, mobile testing, or an OAuth provider that will not accept `http://mybench.localhost` as a redirect URI, tunnel the bench through ngrok:

```bash
fm ngrok mybench --auth-token YOUR_TOKEN --save-token   # first run: save the token
fm ngrok mybench                                        # later runs reuse it
```

The token can also come from `NGROK_AUTHTOKEN` or fm's config, and `fm ngrok` asks whether to save a new one when neither `--save-token` nor `--no-save-token` is passed.

The tunnel lives only while the command runs, and without a paid ngrok plan the URL changes on every run, so anything registered with a third party (a Google OAuth redirect URI, a webhook endpoint) has to be updated each time. It is a development convenience, not a deployment mechanism: to serve a bench publicly, use a server with real DNS and the [Hosting guide](hosting.md) or the [Deployment guide](../deploy/index.md).
