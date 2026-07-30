## `fm auth`

Put an HTTP basic auth prompt in front of a bench: the site, the admin tools, or both.

Two independent surfaces share one credential pair: web covers frappe and socketio (every path bar the admin tools), tools covers /adminer/ and /mailpit/. Both are enforced by the bench nginx, the only route into the bench, so the prompt holds for everything the bench serves. Let's Encrypt HTTP-01 renewal is exempt and keeps working.

--protect is DECLARATIVE: the surfaces passed become the resulting state, so --protect tools alone turns web off again, and --off turns both off. Credentials and allow lists survive either way, so re-enabling asks for nothing (--clear-exemptions empties the allow lists explicitly). A bare fm auth BENCHNAME reports the current state.

Basic auth sends the credentials base64-encoded, not encrypted, on every single request, so on a bench without TLS they are effectively cleartext. Enabling the web surface there is refused unless you pass --insecure, because that surface gates every path including /api; the tools surface only warns, since /adminer/ and /mailpit/ have served behind basic auth over plain http all along. Changing credentials and reading the status never gate.

**Usage**:

```console
$ fm auth BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--protect`: Surface that asks for the password (repeatable). Declarative: the surfaces given become the resulting state, so --protect tools alone turns web off again. web = frappe + socketio (every path bar the admin tools), tools = /adminer/ and /mailpit/.
* `--off`: Turn the prompt off on both surfaces. The credentials stay in the config, so re-enabling asks nothing.
* `--status`: Report which surfaces are protected, with the credentials and allow lists, without changing anything.
* `--user`: Basic auth username, shared by both surfaces. Defaults to 'admin' on the first enable.
* `--password`: Basic auth password. Pass - to read it from stdin instead of the command line, keeping it out of the shell history. A random one is minted on the first enable.
* `--rotate`: Replace the password with a fresh random one, invalidating every browser session that cached the old credentials.
* `--allow-ip`: Address or CIDR that skips the prompt wherever auth applies (repeatable; the addresses given replace the stored list, omitting the flag keeps it). Behind a CDN or external proxy this needs real-IP forwarding to see the visitor instead of the edge.
* `--allow-path`: Absolute path prefix served without a prompt, e.g. /api/method/payment_webhook or /assets (repeatable; the paths given replace the stored list, omitting the flag keeps it). Web surface only.
* `--clear-exemptions`: Empty both allow lists (addresses and paths). Applied before any --allow-ip/--allow-path in the same call, so combining them replaces the lists outright instead of conflicting.
* `--insecure`: Enable the web surface on a bench without TLS anyway, and silence the same warning on the admin tools surface.


## Examples

### Password-protect the whole bench

Puts a basic auth prompt in front of frappe and socketio (every path bar the admin tools) and prints the credentials. Turns the admin tools prompt off, because --protect is declarative: add --protect tools to keep both. On a bench without TLS this is refused unless you add --insecure.

```bash
fm auth mybench --protect web
```

### Protect the admin tools only

Only /adminer/ and /mailpit/ ask for the password; the site itself stays open. This is the default state of a bench, and it works on a plain http bench too (with a warning, no --insecure needed).

```bash
fm auth mybench --protect tools
```

### Set your own credentials

--password - reads the password from stdin (prompted without echo on a terminal, piped in scripts) so it never lands in the shell history. One credential pair serves both surfaces.

```bash
fm auth mybench --protect web --protect tools --user alice --password -
```

### Let the office and a webhook through

Allow-listed addresses skip the prompt wherever auth applies; allow-listed path prefixes are served without a prompt on the web surface (webhooks, health checks). Both are repeatable and replace the stored list; --clear-exemptions empties both lists.

```bash
fm auth mybench --protect web --allow-ip 203.0.113.0/24 --allow-path /api/method/payment_webhook
```

### Rotate the password

Mints a fresh random password, keeps the surfaces and the username as they are, and prints the new credentials. Every existing browser session has to authenticate again.

```bash
fm auth mybench --rotate
```

### Show the state or remove the prompt

Reports which surfaces are protected, the credentials and the allow lists without writing anything (this is also what a bare `fm auth BENCHNAME` does). --off turns both surfaces off while keeping the credentials for later.

```bash
fm auth mybench --status
```

