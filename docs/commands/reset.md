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


**Examples**:

_Drop database and reinstall all apps_
Drops the site's database and reinstalls all apps; destructive and intended for development or recovery.
```bash
fm reset mybench
```

_Reset with custom admin password_
Resets the bench and sets the new administrator password after reinstalling apps.
```bash
fm reset mybench --admin-pass newpassword
```

