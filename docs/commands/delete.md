## `fm delete`

Delete a bench: its containers and volumes, its whole directory, and its TLS certificate.

The database is decided separately. fm can drop the site's schema and user from the global-db container it owns, but a schema on a server fm does not own is always left in place, --delete-db-from-global-db or not.

**Usage**:

```console
$ fm delete BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `-y, --yes`: Delete without the removal confirmation. The database question is asked anyway.
* `--delete-db-from-global-db/--no-delete-db-from-global-db`: Drop the site's schema and user from the global-db container, or keep them. Never touches a database on an external server. fm asks when neither is passed.


## Examples

### Delete a bench and its database

```bash
fm delete mybench --delete-db-from-global-db
```

### Delete the bench but keep the database

The bench is gone; the schema stays in global-db.

```bash
fm delete mybench --no-delete-db-from-global-db
```

### Delete unattended

```bash
fm delete mybench --yes --delete-db-from-global-db
```

