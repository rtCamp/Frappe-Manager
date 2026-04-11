## `fm delete`

Delete a bench and optionally its database from global-db service.

This command removes the bench directory, containers, and all associated data.
By default, asks for confirmation before deletion. Use --yes to skip confirmation.
Optionally delete the bench's database from the global-db service with --delete-db-from-global-db.

**Usage**:

```console
$ fm delete BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `-y, --yes`: Skip confirmation prompts
* `--delete-db-from-global-db/--no-delete-db-from-global-db`: Delete database from global-db service


**Examples**:

_Delete bench_
```bash
fm delete mybench
```

_Delete without confirmation_
```bash
fm delete mybench --yes
```

_Delete bench and its database from global-db_
```bash
fm delete mybench --delete-db-from-global-db
```

