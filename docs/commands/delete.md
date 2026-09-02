## `fm delete`

Delete a whole bench, or one site out of one.

BENCH deletes the bench: every site in it, its containers and volumes, its whole directory, and its TLS certificates. A bench serving more than one site also needs --all-sites and asks for its name typed back, because one word would otherwise destroy several separately named sites.

BENCH/SITE deletes just that site: its schema, its certificate, its proxy entries and its files. The bench and its other sites keep running.

The database is decided separately. fm can drop a site's schema and user from the global-db container it owns, but a schema on a server fm does not own is always left in place, --delete-db-from-global-db or not. A schema fm cannot account for, one whose name is unreadable or whose drop failed, stops the deletion with the bench directory intact, because that directory holds the only record of the schema.

**Usage**:

```console
$ fm delete BENCH(/SITE) [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE to act on one of its sites. Without a site part, the bench's primary site is used.

**Options**:

* `--all-sites`: Required to delete a bench that serves more than one site, and it means every one of them. A single-site bench does not need it, and a bench/site address refuses it because that address already names exactly one site.
* `-y, --yes`: Delete without the removal confirmation, including the typed-name confirmation a multi-site bench asks for. The database question is asked anyway, and --all-sites is still required.
* `--delete-db-from-global-db/--no-delete-db-from-global-db`: Drop the schema and user from the global-db container, or keep them. Applies to every site being deleted that is on the global-db container, and never touches a database on an external server. fm asks when neither is passed.


## Examples

### Delete a bench and its database

```bash
fm delete mybench --delete-db-from-global-db
```

### Delete one site out of a bench

Only that site is removed. The bench and its other sites keep running, so no --all-sites is needed: the address already names exactly one site.

```bash
fm delete mybench/a.example.com
```

### Delete a bench that serves several sites

fm lists every site it is about to destroy, then asks for the bench name typed back.

```bash
fm delete mybench --all-sites
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

### Delete a multi-site bench unattended

--yes skips the confirmation; --all-sites is still required, so no script deletes more than it named.

```bash
fm delete mybench --all-sites --yes --delete-db-from-global-db
```

