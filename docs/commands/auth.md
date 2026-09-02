## `fm auth`

Put an HTTP basic auth prompt in front of a bench: the site, the admin tools, or both.

--protect is declarative: the surfaces you pass become the resulting state, and a bench starts with the admin tools prompting and the site open, so --protect web alone also turns the tools prompt off; name both surfaces to keep both. Credentials and allow lists are kept when a surface goes off, so re-enabling asks for nothing. A bare fm auth BENCH reports the state.

Basic auth sends credentials base64-encoded, not encrypted, so on a bench without TLS they are effectively cleartext: protecting the web surface there needs --insecure.

**Usage**:

```console
$ fm auth BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

**Options**:

* `--protect`: Surface that asks for the password (repeatable). web = frappe and socketio, tools = /adminer/ and /mailpit/.
* `--off`: Turn the prompt off on both surfaces, keeping the credentials.
* `--status`: Report which surfaces are protected, with the credentials and allow lists while a surface is protected. Writes nothing.
* `--user`: Basic auth username, shared by both surfaces. Defaults to 'admin'.
* `--password`: Basic auth password. Pass - to read it from stdin, keeping it out of the shell history. A random one is minted on the first enable.
* `--rotate`: Replace the password with a fresh random one, invalidating browser sessions that cached the old one.
* `--allow-ip`: Address or CIDR that skips the prompt (repeatable; replaces the stored list). Behind a CDN this needs real-IP forwarding, see fm self real-ip.
* `--allow-path`: Absolute path prefix served without a prompt, e.g. /api/method/payment_webhook (repeatable; replaces the stored list). Web surface only.
* `--clear-exemptions`: Empty both allow lists. Applied before any --allow-ip/--allow-path in the same call.
* `--insecure`: Protect the web surface on a bench without TLS anyway, and silence the same warning on the tools surface.


## Examples

### Password-protect the whole bench

Prompts for frappe and socketio, and prints the credentials. Turns the admin tools prompt off: add --protect tools to keep both.

```bash
fm auth mybench --protect web
```

### Protect the admin tools only

Leaves the site open. This is a bench's default state.

```bash
fm auth mybench --protect tools
```

### Set your own credentials

Reads the password from stdin, so it never lands in the shell history.

```bash
fm auth mybench --protect web --protect tools --user alice --password -
```

### Let a webhook through

Exempt paths replace the stored list; omitting the flag keeps it.

```bash
fm auth mybench --protect web --allow-path /api/method/payment_webhook
```

### Show the state, or remove the prompt

--off turns both surfaces off and keeps the credentials for later.

```bash
fm auth mybench --status
```

