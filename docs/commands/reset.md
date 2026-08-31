## `fm reset`

Destroy one site: drop its database and reinstall every app, losing all site data.

The address picks the site. `fm reset BENCH` resets the bench's own site; `fm reset BENCH/SITE` resets exactly SITE and leaves the bench's other sites alone.

Only a site whose database is on the server fm owns can be reset. A site with its own \[database] entry is refused, because that schema is not fm's to drop.

**Usage**:

```console
$ fm reset BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Bench, or bench/site.

**Options**:

* `-y, --yes`: Reset without the confirmation. The site data is gone either way.
* `--admin-pass`: Administrator password for the reinstalled site. Taken from site_config.json, then common_site_config.json, or prompted for, when omitted.


## Examples

### Reset a site to a fresh install

```bash
fm reset mybench
```

### Reset one named site of a bench that serves several

Only that site is reinstalled. The bench and its other sites keep running and keep their data.

```bash
fm reset mybench/shop.example.com
```

### Reset and set a new administrator password

```bash
fm reset mybench --admin-pass 'new-password'
```

### Reset unattended

Skips the confirmation. Nothing else about the reset changes.

```bash
fm reset mybench --yes
```

