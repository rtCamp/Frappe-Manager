# Backup & Restore

Three tools, three surfaces. Knowing which one you are talking to is most of this page.

| Tool | Owns | Commands |
|---|---|---|
| `bench` (inside the container) | site data: the database and the uploaded files | `bench backup`, `bench restore` |
| `fm` (on the host) | insurance around fm's own destructive operations | `fm migrate` backups, `[switch] backup_db`, `fm switch --restore-db`, `fm prune` |
| the host filesystem | getting either of the above off the machine | `cp`, `rsync`, your backup agent |

fm has no site-data backup command and does not wrap `bench backup`. Everything in the first row is Frappe's, reached through `fm shell`.

## Site data: `bench backup`

`fm shell` runs the command in the bench's `frappe` container with the working directory already at `/workspace/frappe-bench`:

```bash
# database only: faster and much smaller
fm shell mybench -c "bench --site mybench.localhost backup"

# database plus every uploaded file
fm shell mybench -c "bench --site mybench.localhost backup --with-files"
```

`--with-files` is a `bench` flag, not an `fm` flag. `--site` is optional (fm runs `bench use <benchname>` at create time, so the bench name is the default site), but naming it is what keeps the command correct on a bench that grows a second site.

### Where backups land

Frappe writes them to the site's own private directory, `sites/<sitename>/private/backups/`. In the container that is an absolute path under `/workspace`, and `/workspace` is a bind mount of the bench's `workspace` directory, so the same files are on the host:

```
container: /workspace/frappe-bench/sites/mybench.localhost/private/backups/
host:      ~/frappe/sites/mybench/workspace/frappe-bench/sites/mybench.localhost/private/backups/
```

Copying a file into that host directory makes it appear inside the container at the same path, which is how you bring a dump in from somewhere else.

Frappe names each artefact `<YYYYMMDD_HHMMSS>-<site slug>-<kind>`, where the slug is the site name with dots replaced by underscores:

| File | Contents |
|---|---|
| `20260824_143000-mybench_localhost-database.sql.gz` | gzipped SQL dump of the whole schema |
| `20260824_143000-mybench_localhost-files.tar` | public file attachments (`--with-files` only) |
| `20260824_143000-mybench_localhost-private-files.tar` | private file attachments (`--with-files` only) |
| `20260824_143000-mybench_localhost-site_config_backup.json` | copy of `site_config.json`, written every time |

!!! warning "Every `bench backup` deletes the older ones"
    Frappe clears that directory before it writes: anything older than `keep_backups_for_hours` (23 hours when the key is absent from the site config) is removed. The backups directory is a staging area, not an archive. Copy the files off the host, or set `keep_backups_for_hours` higher, if you want history.

Automating this is Frappe's job, not fm's. Frappe schedules nothing locally on its own; a recurring backup comes from one of its off-site integrations (S3, Dropbox, Google Drive backup settings), which is also the only sane target, since the staging directory above is inside the bench you are backing up.

## Site data: `bench restore`

`bench restore` is not a merge and not reversible: it drops the site's database **and its database user**, recreates both, then imports the dump. That needs a database login that can drop and create schemas and users, so `bench` asks for the MariaDB root credentials.

Open an interactive shell, because the prompt needs a terminal that `fm shell -c` does not give it:

```bash
fm shell mybench
```

Then, from `/workspace/frappe-bench` where the shell starts:

```bash
bench --site mybench.localhost restore \
  sites/mybench.localhost/private/backups/20260824_143000-mybench_localhost-database.sql.gz \
  --with-public-files  sites/mybench.localhost/private/backups/20260824_143000-mybench_localhost-files.tar \
  --with-private-files sites/mybench.localhost/private/backups/20260824_143000-mybench_localhost-private-files.tar
```

`--with-public-files` and `--with-private-files` are `bench restore` flags, and both take the path to a tar file. At the `MySQL root password:` prompt, the user is `root` and the password is in `~/frappe/services/secrets/db_root_password.txt` on the host, which is where fm keeps the `global-db` root credential. For a scripted restore, pass `--db-root-username` and `--db-root-password` on the command line instead of waiting for the prompt.

!!! danger "An external database has no root credential to hand it"
    On a bench with a `[database]` entry the schema lives on a server fm does not own. `bench restore` will still try to drop the schema and the login and recreate them, and fm holds no administrative credential for that server: `--db-admin-user` is create-time only and is never written to disk. Restoring there is between you and your database provider. See [External Database](external-database.md).

## fm's own backups

These exist so fm can undo fm. Neither of them contains your uploaded files, so neither replaces a `bench backup`.

### Before a migration

`fm migrate` copies each bench's configuration and dumps its database before touching anything:

```
~/frappe/sites/<benchname>/backups/migrations/<DD-Mon-YY--HH-MM-SS>/<fm version>/
```

with `bench_config.toml`, `docker-compose.yml`, `common_site_config.json`, `site_config.json` and a gzipped `db-<benchname>-<date>.sql.gz`. The global services' `docker-compose.yml` is copied to `~/frappe/backups/migrations/<timestamp>/<fm version>/`.

When a bench fails to migrate, fm restores **the copied configuration files only**. The SQL dump is never imported automatically; it is there for you to restore by hand with `bench restore` if a migration damaged data. `--on-failure` picks the policy: `prompt` (default) asks, `archive` sets the failed benches aside and keeps the rest migrated, `rollback` reverts every bench. A single-bench run always rolls back.

Skipping the backup is possible and rarely wise:

```bash
fm migrate --skip-backup-for mybench            # this bench only, comma-separated for several
fm migrate all --skip-all-backup                # every bench
```

!!! danger
    With no backup there is nothing to restore from, so use these only when the backup itself is what fails.

### Before a deploy or switch

An image bench dumps its database before every deploy that can change the schema, controlled by [`[switch] backup_db`](../reference/configuration.md#deploy-tables) (`true` by default, or `"auto"` to dump only when the deploy will run `bench migrate`). One directory per release, holding an uncompressed `db-<schema>.sql` plus copies of `site_config.json` and `common_site_config.json`:

```
~/frappe/sites/<benchname>/backups/deploy-<YYYYMMDDHHMMSS>/
```

The dump is skipped, with a warning rather than a failure, if the frappe container is not running or the schema name cannot be resolved.

That dump is what `fm switch <benchname> --previous --restore-db` imports when a migration is the thing that broke. Setting `[switch] rollback_db = true` makes fm import it automatically when a deploy fails; it is off by default, because `bench migrate` is resumable and an import is not. `fm prune` deletes the dumps of releases it retires.

!!! warning "An import over an external schema asks first"
    `--restore-db` imports **over** the live schema, and a Frappe dump starts by dropping every table it is about to write. When the schema is on a server fm does not own, fm demands a typed confirmation naming the host, the schema and its current table count, and refuses outright in non-interactive mode.

## Destroying data on purpose

```bash
fm reset mybench    # drop the site database and reinstall every app
```

`fm reset` runs `bench reinstall`, so all site data is gone and only the app code survives. It works only for a site on the `global-db` container fm owns: a bench with a `[database]` entry is refused, because that schema is not fm's to drop. `fm delete` draws the same line, and never drops an external schema whatever `--delete-db-from-global-db` says.

---

!!! info "See also"
    - [Deployment](../deploy/index.md#releases-history-and-pruning): release history, the per-release dump, and what `fm prune` keeps
    - [Migrations reference](../reference/migrations.md): how fm migrations work and how rollbacks are triggered
    - [fm migrate command](../commands/migrate.md): all migration flags
