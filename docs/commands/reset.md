## `fm reset`

Drop database and reinstall all apps

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
```bash
fm reset mybench
```

_Reset with custom admin password_
```bash
fm reset mybench --admin-pass newpassword
```

