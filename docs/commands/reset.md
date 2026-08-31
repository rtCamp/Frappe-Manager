## `fm reset`

Destroy a site: drop its database and reinstall every app, losing all site data.

Only sites on the database server fm owns can be reset. A bench with its own \[database] entry is refused, because that schema is not fm's to drop.

**Usage**:

```console
$ fm reset BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `-y, --yes`: Reset without the confirmation. The site data is gone either way.
* `--admin-pass`: Administrator password for the reinstalled site. Taken from site_config.json, then common_site_config.json, or prompted for, when omitted.


## Examples

### Reset a site to a fresh install

```bash
fm reset mybench
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

