# `fm maintenance`

Put a bench's domains, aliases included, behind a maintenance page.

**Usage**:

```console
$ fm maintenance COMMAND [ARGS]...
```

| Command | Description |
|---|---|
| [`fm maintenance enable`](#fm-maintenance-enable) | Put a bench's domains, aliases included, behind a maintenance page. |
| [`fm maintenance disable`](#fm-maintenance-disable) | Take the addressed domains out of maintenance and serve them again. |
| [`fm maintenance status`](#fm-maintenance-status) | Report maintenance state per domain, with the bypass URL. |

## `fm maintenance enable`

Put a bench's domains, aliases included, behind a maintenance page.

fm maintenance enable BENCH covers every hostname the bench serves. fm maintenance enable BENCH/SITE covers that one site's own name and its aliases, leaving the bench's other sites serving: the page is written per domain in the shared proxy, so one site can be down while its neighbours are up.

Enabling prints a secret bypass URL: open it once and a cookie lets you through to the real site for a day while everyone else gets the page (visit /fm-bypass/off to drop it sooner).

Each enable rewrites the settings from the flags you pass, so repeat the ones you still want; only the bypass token carries over, unless you ask for --rotate-token.

**Usage**:

```console
$ fm maintenance enable BENCH(/SITE) [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE for one site's hostnames only.

**Options**:

* `--response-code INTEGER`: HTTP status code served while maintenance is on (400-599).  [default: 503]
* `--retry-after INTEGER`: Retry-After header in seconds; 0 omits it.  [default: 300]
* `--allow-ip TEXT`: Client IP that reaches the real site (repeatable; single addresses, no CIDR). Behind a CDN see fm services real-ip.
* `--allow-path TEXT`: Request path served the real site, e.g. /api/method/ping (repeatable). Exact match; append * for a prefix.
* `--message TEXT`: Text shown on fm's built-in maintenance page.
* `--page PATH`: HTML file served as the page, instead of --message. A bench's configs/maintenance.html is used automatically.
* `--rotate-token`: Mint a fresh bypass token, invalidating every bypass URL and cookie already handed out.  [default: false]
* `-y, --yes`: Serve the maintenance page without asking for confirmation.  [default: false]

### Examples

#### Put a bench into maintenance

```bash
fm maintenance enable mybench
```

#### Put one site of a multi-site bench into maintenance

```bash
fm maintenance enable mybench/shop.example.com
```

#### Let the office and a payment webhook through

```bash
fm maintenance enable mybench --allow-ip 203.0.113.7 --allow-path '/api/method/payment_webhook*'
```

#### Say when you will be back

```bash
fm maintenance enable mybench --message 'Back at 17:00 UTC' --retry-after 1800
```

## `fm maintenance disable`

Take the addressed domains out of maintenance and serve them again.

A bare bench name covers every domain it serves; BENCH/SITE covers that one site's own name and its aliases, leaving the bench's other sites as they are.

**Usage**:

```console
$ fm maintenance disable BENCH(/SITE)
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE for one site's hostnames only. A bare bench name covers every domain it serves.

### Examples

#### Bring the bench back

```bash
fm maintenance disable mybench
```

#### Bring one site back, leaving the bench's others in maintenance

```bash
fm maintenance disable mybench/shop.example.com
```

## `fm maintenance status`

Report maintenance state per domain, with the bypass URL.

Writes nothing. Without a bench, lists every domain currently in maintenance across every bench.

**Usage**:

```console
$ fm maintenance status BENCH(/SITE)
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE for one site's hostnames only. Omit it to list every domain in maintenance, across every bench.

### Examples

#### Check one bench

```bash
fm maintenance status mybench
```

#### Check one site's hostnames only

```bash
fm maintenance status mybench/shop.example.com
```

#### See every domain in maintenance, across every bench

```bash
fm maintenance status
```
