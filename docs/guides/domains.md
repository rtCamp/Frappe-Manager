# Domains & Remote Access

How requests reach a bench, how to serve it on more than one domain, and how to expose a local bench to the internet.

## How routing works

Every bench registers its domains with the shared **nginx-proxy** (one per machine, listening on ports 80/443). The proxy routes each request by its `Host:` header: no per-bench ports, no manual proxy config. See [Architecture](../reference/architecture.md) for the full topology.

`*.localhost` names resolve to `127.0.0.1` automatically on modern systems, so a bench named `mybench.localhost` works in the browser with zero DNS setup.

```bash
fm create mybench            # http://mybench.localhost just works
fm create shop.example.com   # any domain works -- DNS is your job (see below)
```

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
- HTTPS for aliases: certificates are per-domain; see the [SSL guide](./ssl.md).
- Aliases are stored as [`alias_domains`](../reference/configuration.md#alias-domains) in `bench_config.toml`.

## Making a domain resolve

| Where the bench runs | What you need |
|---|---|
| your machine, `*.localhost` name | nothing (resolves automatically) |
| your machine, real domain name | an `/etc/hosts` entry: `127.0.0.1 shop.example.com` |
| a server | real DNS: point an `A`/`AAAA` (or `CNAME`) record at the server, then `fm ssl add` for HTTPS |

## Exposing a local bench to the internet

For webhooks, mobile testing, or sharing work in progress, tunnel a local bench through ngrok:

```bash
fm ngrok mybench    # prints a public URL tunneled to the bench
```

Requires an ngrok auth token (flag or config; see `fm ngrok --help`). The tunnel lives while the command runs; it is a development convenience, not a deployment mechanism. To actually host a bench publicly, use a server with real DNS and the [Hosting guide](hosting.md) or the [Deployment guide](../deploy/index.md).

## Google OAuth during local development

Google OAuth requires a public HTTPS redirect URI; a local `http://mybench.localhost` URL will not be accepted. Tunnel the bench through ngrok to get one:

```bash
fm ngrok mybench --auth-token YOUR_TOKEN --save-token   # first run: save the token
fm ngrok mybench                                        # later runs reuse the saved token
```

Copy the public HTTPS URL that ngrok prints and add it as an **Authorized Redirect URI** in the Google Cloud Console.

!!! tip
    ngrok URLs are ephemeral unless you have a paid ngrok account. Update the redirect URIs in Google Cloud each time the URL changes.
