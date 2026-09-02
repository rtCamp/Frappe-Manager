## `fm maintenance`

Put every domain of a bench, aliases included, behind a maintenance page.

Enabling prints a secret bypass URL: open it once and a cookie lets you through to the real site while everyone else gets the page (visit /fm-bypass/off to drop it again).

Each enable rewrites the settings from the flags you pass, so repeat the ones you still want; only the bypass token carries over, unless you ask for --rotate-token.

**Usage**:

```console
$ fm maintenance BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Optional with --status, which then lists every domain in maintenance.

**Options**:

* `--off`: Take every domain out of maintenance and serve the bench again.
* `--status`: Report maintenance state per domain, with the bypass URL.
* `--response-code`: HTTP status code served while maintenance is on (400-599).
* `--retry-after`: Retry-After header in seconds; 0 omits it.
* `--allow-ip`: Client IP that reaches the real site (repeatable; single addresses, no CIDR). Behind a CDN see fm self real-ip.
* `--allow-path`: Request path served the real site, e.g. /api/method/ping (repeatable). Exact match; append * for a prefix.
* `--message`: Text shown on fm's built-in maintenance page.
* `--page`: HTML file served as the page, instead of --message. A bench's configs/maintenance.html is used automatically.
* `--rotate-token`: Mint a fresh bypass token, invalidating every bypass URL and cookie already handed out.


## Examples

### Put a bench into maintenance

```bash
fm maintenance mybench
```

### Let the office and a payment webhook through

```bash
fm maintenance mybench --allow-ip 203.0.113.7 --allow-path '/api/method/payment_webhook*'
```

### Say when you will be back

```bash
fm maintenance mybench --message 'Back at 17:00 UTC' --retry-after 1800
```

### Check the state

```bash
fm maintenance mybench --status
```

### Bring the bench back

```bash
fm maintenance mybench --off
```

