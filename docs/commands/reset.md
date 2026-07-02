## `fm reset`

Drop database and reinstall all apps.

Intended for resetting a site to a clean state; this operation removes site data.

**Usage**:

```console
$ fm reset BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--admin-pass`: Password for the 'Administrator' User.


## Examples

### Drop database and reinstall all apps

Drops the site's database and reinstalls all apps; destructive and intended for development or recovery.

```bash
fm reset mybench
```

### Reset with custom admin password

Resets the bench and sets the new administrator password after reinstalling apps.

```bash
fm reset mybench --admin-pass newpassword
```

