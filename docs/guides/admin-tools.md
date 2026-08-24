# Admin tools (Mailpit & Adminer)

Mailpit catches every mail the site sends and Adminer browses its database. Both are path-routed under the bench URL, so neither publishes a host port.

Enable admin tools:

```bash
fm update mybench --admin-tools enable
```

Disable:

```bash
fm update mybench --admin-tools disable
```

Access:

- Mailpit: http://mybench.localhost/mailpit/
- Adminer: http://mybench.localhost/adminer/

Both sit behind an HTTP basic auth prompt by default. `fm info mybench` prints the credentials.

Adminer opens on one-click login cards rather than a blank login form: one per site database, plus the bench's Redis cache and Redis queue. The cards are read from the mounted `sites` directory on every request, so a password change (a restore, a rotation) is picked up without regenerating anything.

!!! warning
    Admin tools are enabled on `dev` benches and disabled on `prod` benches at create time. `fm update --environment` does not toggle them: disable explicitly before going live.

## Mailpit as the site's mail server

`--mailpit-as-default-mail-server` writes `mail_server`, `mail_port` and `disable_mail_smtp_authentication` into `common_site_config.json`. It is only read on the `--admin-tools enable` path, so pass both flags in the same call, whether or not the tools are already on:

```bash
fm update mybench --admin-tools enable --mailpit-as-default-mail-server
```

`fm update mybench --admin-tools disable` takes those three keys back out, but only where they still hold fm's values: a `mail_server` you have since pointed at a real relay is left alone.

If you need the SMTP endpoint manually (inside the Docker network): host `fm__<benchname>__mailpit`, port 1025, where `<benchname>` is the full bench name with dots replaced by underscores. For example, bench `mybench` (full name `mybench.localhost`) → host `fm__mybench_localhost__mailpit`.

!!! note
    Mailpit keeps up to 5,000 messages. Older messages are automatically deleted when the limit is reached.

## The basic auth prompt

`fm auth` owns the prompt. A bench has two independently protectable surfaces, both enforced by the bench nginx against one credential pair:

| Surface | Covers |
| --- | --- |
| `tools` | `/adminer/` and `/mailpit/` only. On by default. |
| `web` | frappe and socketio, so every other path including `/api/*`. Off by default. The ACME challenge path opts out, so certificate renewal keeps working. |

`--protect` is declarative: the surfaces you name become the resulting state. `--protect tools` alone therefore turns `web` back off, and protecting both takes both flags.

```bash
# Password-protect the whole bench, admin tools included
fm auth mybench --protect web --protect tools

# Back to the default: tools prompt, site open
fm auth mybench --protect tools

# Report the current state, writing nothing
fm auth mybench --status
```

Credentials and allow lists are kept when a surface goes off, so re-enabling asks for nothing. `--off` is the shorthand for turning both surfaces off while keeping them, and a bare `fm auth mybench` reports the state without writing.

### Credentials

One username and password serve both surfaces. `--user` sets the name (default `admin`), and a random password is minted the first time a surface goes on. To set your own without leaving it in the shell history, read it from stdin:

```bash
fm auth mybench --protect web --protect tools --user alice --password -
```

`--rotate` replaces the password with a fresh random one, which invalidates any browser session that cached the old one.

### TLS

Basic auth sends the credentials base64-encoded on every request, not encrypted. On a bench with no certificate:

- `--protect web` is refused outright, because it would put those credentials in front of every path including `/api`. Add HTTPS with `fm ssl add mybench`, or pass `--insecure` to accept it.
- `--protect tools` warns and proceeds, since that is fm's own default state. `--insecure` silences the warning.

### Exemptions

Two allow lists let specific callers skip the prompt, and they are OR'd: a request matching either one is served without a challenge.

```bash
# Addresses or CIDRs, on whichever surfaces are protected
fm auth mybench --protect web --allow-ip 203.0.113.4 --allow-ip 10.0.0.0/8

# Absolute path prefixes, web surface only
fm auth mybench --protect web --allow-path /api/method/payment_webhook
```

Each flag **replaces** its stored list rather than appending, and omitting the flag leaves that list alone. `--clear-exemptions` empties both, and is applied before any `--allow-ip`/`--allow-path` in the same call.

!!! note "`--allow-path` is web-surface only"
    Passing it when the resulting state does not protect `web` is an error, not a silent no-op. There is no path-level exemption for the admin tools: `/adminer/` and `/mailpit/` are all-or-nothing.

!!! warning "Allow lists behind a proxy"
    `--allow-ip` matches the address nginx sees. Behind a CDN or load balancer that is the proxy, not your client, until real-IP forwarding is configured. See `fm self real-ip`.

### Two things that catch people out

- `fm auth --protect tools` on a bench whose admin tools are **disabled** stores the intent and warns: there are no `/adminer/` and `/mailpit/` locations to gate yet. It starts applying once you run `fm update mybench --admin-tools enable`.
- `--protect web` is refused on a bench whose nginx conf predates the `Authorization`-header fix, because nginx would forward the credentials it just checked and frappe would answer 401 to every authenticated request. `fm migrate` re-renders the conf on a mount bench; an image bench needs `fm bake` then `fm switch`. The `tools` surface is unaffected either way.

See also: [Environments](environments.md) for the dev/prod defaults behind these tools, [`[auth]`](../reference/configuration.md#auth) for the config keys `fm auth` writes, and [Architecture](../reference/architecture.md) for how the tools are routed inside the bench.
