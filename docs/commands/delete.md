## `fm delete`

Delete a bench and optionally its database from global-db service.

Removes the bench directory, containers, and associated data. Use --yes to avoid confirmation prompts.

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

_Delete a bench_
Deletes the bench directory and associated containers. This is destructive and will remove local bench data.
```bash
fm delete mybench
```

_Delete without confirmation_
Performs deletion without interactive confirmation. Use with caution in scripts or automation.
```bash
fm delete mybench --yes
```

_Delete bench and its database from global-db_
Also deletes the bench's database from the global-db service. This permanently removes stored site data.
```bash
fm delete mybench --delete-db-from-global-db
```

