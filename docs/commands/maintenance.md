## `fm maintenance`

Toggle a proxy-level maintenance page for a bench (all domains, aliases included).

Maintenance is enforced at the global nginx proxy, so it holds even while the bench itself is stopped, restarting, or being deployed. Everyone gets the maintenance page (503 by default; tune with --response-code) and /api/* clients get a JSON body; opening the printed secret bypass URL sets a cookie that lets YOU through to the real site (open /fm-bypass/off to drop it). Allow-listed IPs and paths (webhooks, health checks) pass through as well.

Re-running enable replaces the previous settings (code, allow lists, page); the bypass token is kept unless --rotate-token is given. The page comes from --page, --message, the bench's configs/maintenance.html, or fm's default, in that order.

**Usage**:

```console
$ fm maintenance BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench. Optional with --status (lists every domain in maintenance).

**Options**:

* `--off`: Disable maintenance mode and serve the bench again.
* `--status`: Show maintenance state per domain without changing anything.
* `--response-code`: HTTP status code served while maintenance is on (400-599). Default 503 Service Unavailable.
* `--retry-after`: Retry-After header value in seconds on maintenance responses (tells crawlers the outage is temporary). 0 disables the header.
* `--allow-ip`: Client IP that bypasses maintenance (repeatable). Exact IPs only; behind a CDN or external proxy this matches the forwarding IP unless real-IP forwarding is configured.
* `--allow-path`: Request path that bypasses maintenance, e.g. payment webhooks or health checks (repeatable). Exact match; append * for a prefix match.
* `--message`: Custom text shown on fm's built-in maintenance page.
* `--page`: Fully custom maintenance page: path to an HTML file served as-is. Tip: a configs/maintenance.html in the bench directory is picked up automatically on every enable.
* `--rotate-token`: Generate a fresh bypass token, invalidating every previously issued bypass cookie/URL.


## Examples

### Put a bench into maintenance

Serves a maintenance page for every domain of the bench (aliases included) at the global nginx proxy, and prints a secret bypass URL that lets you keep using the real site while everyone else sees the page.

```bash
fm maintenance mybench
```

### Maintenance with team and webhook exceptions

The office IP and the payment webhook keep reaching the real bench while everyone else sees the page with a Retry-After of 30 minutes.

```bash
fm maintenance mybench --allow-ip 203.0.113.7 --allow-path '/api/method/payment_webhook*' --retry-after 1800
```

### Custom page or message

Injects the text into fm's maintenance page. Use --page FILE for fully custom HTML, or drop a configs/maintenance.html into the bench directory to make it the permanent default for this bench.

```bash
fm maintenance mybench --message 'Upgrading the database, back at 17:00 UTC'
```

### Maintenance with a different status code

Serves the maintenance page with HTTP 404 instead of 503, e.g. to make the site look absent instead of temporarily down.

```bash
fm maintenance mybench --response-code 404
```

### Show maintenance state

Shows whether maintenance is active per domain and reprints the bypass URL. Omit the bench name to list every domain currently in maintenance.

```bash
fm maintenance mybench --status
```

### Bring the bench back

Removes the maintenance page from every domain; visitors reach the bench again immediately.

```bash
fm maintenance mybench --off
```

