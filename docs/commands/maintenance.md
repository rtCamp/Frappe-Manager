## `fm maintenance`

Toggle a proxy-level maintenance page for a bench (all domains, aliases included).

Maintenance is enforced at the global nginx proxy, so it holds even while the bench itself is stopped, restarting, or being deployed. Everyone gets the maintenance page (503 by default; tune with --response-code); opening the printed secret bypass URL sets a cookie that lets YOU through to the real site (open /fm-bypass/off to drop it).

**Usage**:

```console
$ fm maintenance BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--off`: Disable maintenance mode and serve the bench again.
* `--status`: Show maintenance state per domain without changing anything.
* `--response-code`: HTTP status code served while maintenance is on (400-599). Default 503 Service Unavailable.


## Examples

### Put a bench into maintenance

Serves a maintenance page for every domain of the bench (aliases included) at the global nginx proxy, and prints a secret bypass URL that lets you keep using the real site while everyone else sees the page.

```bash
fm maintenance mybench
```

### Show maintenance state

Shows whether maintenance is active per domain and reprints the bypass URL.

```bash
fm maintenance mybench --status
```

### Bring the bench back

Removes the maintenance page from every domain; visitors reach the bench again immediately.

```bash
fm maintenance mybench --off
```

### Maintenance with a different status code

Serves the maintenance page with HTTP 404 instead of 503, e.g. to make the site look absent instead of temporarily down.

```bash
fm maintenance mybench --response-code 404
```

