# `fm auth`

Put an HTTP basic auth prompt in front of a bench: the site, the admin tools, or both.

**Usage**:

```console
$ fm auth COMMAND [ARGS]...
```

| Command | Description |
|---|---|
| [`fm auth enable`](#fm-auth-enable) | Put an HTTP basic auth prompt in front of a bench: the site, the admin tools, or both. |
| [`fm auth disable`](#fm-auth-disable) | Stop a surface asking for a password, keeping the credentials for later. |
| [`fm auth status`](#fm-auth-status) | Report which surfaces are protected, with the credentials and allow lists while a surface is protected. |

## `fm auth enable`

Put an HTTP basic auth prompt in front of a bench: the site, the admin tools, or both.

--web and --tools select which surfaces this acts on, and naming one says nothing about the other: 'fm auth enable BENCH --web' leaves the admin tools exactly as they were. Naming neither acts on both. Credentials and allow lists are kept when a surface goes off, so re-enabling asks for nothing.

BENCH/SITE protects the web surface of one site, with credentials of its own, and leaves the bench's other sites serving as before. A site with no auth of its own follows the bench, so 'fm auth enable BENCH' still covers every site. --tools takes no site part: one Adminer and one Mailpit serve the whole bench, on every hostname it has.

Basic auth sends credentials base64-encoded, not encrypted, so on a bench without TLS they are effectively cleartext: protecting the web surface there needs --insecure. The certificate checked is the one for the hostname you named.

**Usage**:

```console
$ fm auth enable BENCH(/SITE) [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE for one of its sites. Without a site part the whole bench is addressed: every site that has no auth of its own follows it.

**Options**:

* `--web`: Act on the web surface: frappe and socketio.  [default: false]
* `--tools`: Act on the admin tools surface: /adminer/ and /mailpit/. Bench-wide, so it takes no site part.  [default: false]
* `--user TEXT`: Basic auth username for the scope you named: both surfaces of the bench, or that one site. Defaults to 'admin'.
* `--password TEXT`: Basic auth password. Pass - to read it from stdin, keeping it out of the shell history. A random one is minted on the first enable.
* `--rotate`: Replace the password with a fresh random one, invalidating browser sessions that cached the old one.  [default: false]
* `--allow-ip TEXT`: Address or CIDR that skips the prompt (repeatable; replaces the stored list). Behind a CDN this needs real-IP forwarding, see fm services real-ip.
* `--allow-path TEXT`: Absolute path prefix served without a prompt, e.g. /api/method/payment_webhook (repeatable; replaces the stored list). Web surface only.
* `--clear-exemptions`: Empty both allow lists. Applied before any --allow-ip/--allow-path in the same call.  [default: false]
* `--insecure`: Protect the web surface on a bench without TLS anyway, and silence the same warning on the tools surface.  [default: false]

### Examples

#### Password-protect the whole bench

Both surfaces prompt: frappe and socketio, and /adminer/ and /mailpit/. Prints the credentials.

```bash
fm auth enable mybench
```

#### Protect the site, leaving the admin tools as they are

Naming a surface acts on that surface only; the other keeps whatever state it had.

```bash
fm auth enable mybench --web
```

#### Protect one site of a bench

That site's hostnames prompt with credentials of its own; the bench's other sites keep serving exactly as before. A site with no auth of its own follows the bench, so 'fm auth enable mybench --web' still covers every site.

```bash
fm auth enable mybench/b.example.com --web
```

#### Set your own credentials

Reads the password from stdin, so it never lands in the shell history.

```bash
fm auth enable mybench --user alice --password -
```

#### Let a webhook through

Exempt paths replace the stored list; omitting the flag keeps it.

```bash
fm auth enable mybench --web --allow-path /api/method/payment_webhook
```

## `fm auth disable`

Stop a surface asking for a password, keeping the credentials for later.

--web and --tools select which surfaces this acts on, and naming one says nothing about the other: 'fm auth disable BENCH --web' leaves the admin tools prompting. Naming neither acts on both.

Credentials and allow lists are kept, so 'fm auth enable' afterwards asks for nothing.

**Usage**:

```console
$ fm auth disable BENCH(/SITE) [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE for one of its sites. Without a site part the whole bench is addressed: every site that has no auth of its own follows it.

**Options**:

* `--web`: Act on the web surface: frappe and socketio.  [default: false]
* `--tools`: Act on the admin tools surface: /adminer/ and /mailpit/. Bench-wide, so it takes no site part.  [default: false]

### Examples

#### Stop asking for a password anywhere on the bench

Both surfaces stop prompting. The credentials stay stored and apply again on the next enable.

```bash
fm auth disable mybench
```

#### Open the site, keeping the admin tools protected

Naming a surface acts on that surface only; the other keeps whatever state it had.

```bash
fm auth disable mybench --web
```

#### Stop one site prompting

```bash
fm auth disable mybench/b.example.com
```

## `fm auth status`

Report which surfaces are protected, with the credentials and allow lists while a surface is protected.

Writes nothing. A site with no auth of its own follows the bench, and is reported as inherited.

**Usage**:

```console
$ fm auth status BENCH(/SITE)
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE for one of its sites. Without a site part the whole bench is addressed: every site that has no auth of its own follows it.

### Examples

#### Show which surfaces of a bench ask for a password

```bash
fm auth status mybench
```

#### Show one site's own auth

A site with no auth of its own reports the bench's, and says so.

```bash
fm auth status mybench/b.example.com
```
