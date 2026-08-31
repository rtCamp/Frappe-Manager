# External Database

By default every site fm creates lives on `global-db`, the MariaDB container fm runs and owns. Point a site at your own server instead when you want a managed database, a replicated one, or one your DBA already administers.

An external database is declared per site, not per bench, as a [`[sites."<sitename>".database]`](../reference/configuration.md#sites-database) table in `bench_config.toml`. No table means that site is on `global-db`, which is the only switch there is.

## What fm refuses to do on a server it does not own

Read this first: it is the part you have to trust, and it is deliberate rather than accidental. fm holds the site's own database password (it is in `site_config.json`, where Frappe needs it), and the grant Frappe asks for includes `DROP` at schema scope. fm is therefore perfectly capable of destroying your schema and chooses not to.

| Operation | On `global-db` | On your server |
|---|---|---|
| `fm reset <bench>` | drops the schema and reinstalls every app | **refused**: `bench reinstall` would drop a schema that is not fm's |
| `fm delete <bench>/<site> --delete-db-from-global-db` | drops that site's schema and the user | schema and user are **left in place**, flag or no flag |
| `fm delete <bench> --delete-db-from-global-db` | drops the schema and the user of every site in the bench | any schema on your server is **left in place**, flag or no flag |
| `fm switch <bench> --restore-db` | imports the dump | typed confirmation naming the host, the schema and its current table count; **refused** in non-interactive mode |
| `fm create` into a schema that already has tables | n/a: fm creates the schema | **refused**, unless you pass `--attach-existing-site` |
| `fm create` with a login that already exists and a password fm minted | n/a | **refused**: Frappe's `CREATE USER IF NOT EXISTS` would keep the old password and the site would be unconnectable |

The last two are worth spelling out. fm will not `ALTER USER` on a database it does not own, so it cannot repair a login for you: if the schema already exists, pass `--db-password` with the existing login's password and drop the admin credentials. And it never sends the `global-db` root credential, which means nothing on your server, anywhere near it.

## Server preconditions

`fm create` probes the server from inside the bench container and reports every check it ran. The probe happens early on purpose: the bench directory and its compose file exist by then and are cleaned up if the probe refuses, but cloning apps, installing dependencies and building assets are all still ahead. The blocking checks:

- **MariaDB, any flavour, managed or self-hosted.** MySQL is not a supported Frappe `db_type` and normal operation emits MariaDB-only SQL. Azure retired Database for MariaDB, so an Azure managed instance today is almost certainly MySQL.
- **Version 10.6 or newer.**
- **`innodb_read_only_compressed = 0`.** Core doctypes declare `ROW_FORMAT=Compressed`; with the variable ON a create fails outright and an attached site breaks on its first write. It defaulted to ON in MariaDB 10.6.1 through 10.6.5. On a managed provider this lives in a parameter group; elsewhere in `my.cnf` or a command flag.
- **A real hostname reachable from the bench container.** `localhost` and `127.0.0.1` resolve through a local socket, which silently overrides host and port.
- **TLS, with a CA, whenever the server enforces it.** Managed providers usually do: `require_secure_transport` is ON by default on RDS for MariaDB 11.8 and later, and DigitalOcean accepts nothing else.

`character_set_server = utf8mb4` with `collation_server = utf8mb4_unicode_ci` is checked too, but only warns, since Frappe forces both per connection and per table.

## Creating a bench on it

Two credential paths, and fm detects which one applies from the server rather than asking you for a mode.

=== "The schema already exists"

    You (or your provider's console) created the schema, the login and the grant. fm only needs the site login:

    ```bash
    fm create mybench \
      --db-host db.example.com \
      --db-name app_prod \
      --db-password - \
      --db-ca /etc/ssl/rds-bundle.pem
    ```

    The schema must exist and hold no tables; fm then uses it as is and opens no administrative connection at all. One with tables is refused unless you are attaching (below), and so is an absent schema, because on this path fm has no credential to create one with.

=== "fm provisions it"

    Give fm an administrative login and Frappe creates the schema, the site user and the grant in one shot:

    ```bash
    fm create mybench \
      --db-host db.example.com \
      --db-name app_prod \
      --db-admin-user admin \
      --db-admin-password - \
      --db-ca /etc/ssl/rds-bundle.pem
    ```

    That login needs `CREATE`, `CREATE USER`, `RELOAD` and `GRANT OPTION` at global scope; `RELOAD` because Frappe's setup ends in an unconditional `FLUSH PRIVILEGES`. It is used once, at create time, and is never written to disk. Add `--db-password` as well to choose the new user's password instead of letting fm generate one.

    The schema must not exist yet. If it does, fm refuses the admin credentials rather than `ALTER USER` its login into something it knows; use the other path with `--db-password`.

`-` on any of `--db-password`, `--db-admin-password` and `--encryption-key` reads that secret from stdin, keeping it out of your shell history. Passwords are never config, so a `--config` overlay cannot carry them even though it can carry the `[database]` endpoint.

Some rules the CLI enforces before it connects to anything:

- `--db-host` requires `--db-name`: the schema on that server this site lives in.
- Every other endpoint flag (`--db-port`, `--db-name`, `--db-user`, `--db-ca`, `--db-no-verify-hostname`) requires `--db-host`. The endpoint is given as a whole or not at all.
- `--db-admin-user` and `--db-admin-password` go together, and neither combines with `--attach-existing-site`.
- `--db-user` defaults to the schema name, and on a **v15** bench it must equal the schema name: v15 has no `db_user` config key, so a different login is not representable. Use v16 if you need one.

## TLS

`--db-ca` takes a host path to the CA bundle that signed the server certificate. fm copies it into the bench, points Frappe's Python driver at it, and writes a client option file so the `mariadb` CLI (which Frappe shells out to for dumps, restores and `bench mariadb`) uses it too. Hostname verification is on unless you pass `--db-no-verify-hostname`, which requires `--db-ca`: without a CA there is no TLS at all, so there is no certificate whose hostname could be checked.

Providers rotate CAs on a schedule. Refresh a rotated one with:

```bash
fm update mybench --db-ca /etc/ssl/rds-bundle-2027.pem
```

Do not hand-edit the copied PEM. `fm update` refreshes the per-site certificate, the bench-wide bundle that dump processes read, and the recorded path together; editing one of the three leaves the site working while backups stay broken.

## Attaching to a site that already exists

If the schema already holds a Frappe site, `--attach-existing-site` builds the bench around it and writes nothing at all to the database:

```bash
fm create mybench \
  --db-host db.example.com \
  --db-name app_prod \
  --db-password - \
  --db-ca /etc/ssl/rds-bundle.pem \
  --attach-existing-site \
  --encryption-key -
```

`new-site` is never called, and neither is `bench migrate` or `install-app`: the only statements fm sends are its read-only probe queries. fm verifies the schema really is a Frappe schema first, because attaching to someone else's data and reporting success is the failure mode that matters here.

Pass the original site's `encryption_key`. Without it Frappe mints a new one and every existing encrypted secret stops being readable. Two `-` flags mean two secrets: on a terminal fm prompts for each in turn, and from a pipe it reads one line per flag, in the order the flags are listed above.

## External redis

The same flag family covers redis, per bench rather than per site:

```bash
fm create mybench --redis-cache redis://r.example:6379/0 --redis-queue redis://r.example:6379/1
```

Both are required together, and they must not point at the same logical index: a restore mass-deletes the cache index and would take the queue with it. With them set, fm suppresses its own redis containers. The keys land in [`[redis]`](../reference/configuration.md#redis).

## Moving an existing bench

There is no fm command that repoints a bench's site at a different server; `[database]` is written at create time and only its `ca` is editable afterwards. Hand-editing `site_config.json` is not a substitute: fm writes that file's database keys from `bench_config.toml` at create time and never reads them back, so an edited endpoint leaves fm and the site disagreeing. The guards above key on `[database]`, the CA and its client option file are installed per configured site, and none of that follows an edit fm cannot see. Back up the site, create a new bench with the flags above, and restore into it. Both halves are `bench` operations, and `bench restore` needs a login that can drop and recreate the schema on the target server, which fm does not hold for you. See [Backup & Restore](backup-restore.md).

!!! warning
    Rehearse on a throwaway schema first. A restore drops every table it is about to write.

---

!!! info "See also"
    - [fm create command](../commands/create.md): every external database and redis flag
    - [Architecture](../reference/architecture.md): how a bench reaches its database
    - [Configuration](../reference/configuration.md#sites-database): the `[sites."<site>".database]` and [`[redis]`](../reference/configuration.md#redis) keys, key by key
