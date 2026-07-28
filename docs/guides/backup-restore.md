# Backup & Restore

Frappe Manager does not manage your Frappe site data backups directly - that is Frappe's job. FM's role is to make it easy to trigger backups and to keep your bench configuration safe during migrations.

## Backing up your site data

### From the command line

The quickest way to take a full backup including all uploaded files:

```bash
fm shell mybench -c "bench backup --with-files"
```

Without `--with-files` you get SQL only (faster, smaller):

```bash
fm shell mybench -c "bench backup"
```

### From the Frappe web UI

Go to **Setup → Download Backup** from inside your Frappe site. This triggers the same backup and gives you a download link.

### Where backups are stored

Bench backups land inside the container at `/workspace/frappe-bench/sites/<sitename>/private/backups/`. On the host that maps to:

```
~/frappe/sites/<benchname>/workspace/frappe-bench/sites/<sitename>/private/backups/
```

Each backup is a set of three files:

| File | Contents |
|---|---|
| `<timestamp>-database.sql.gz` | Compressed SQL dump of the entire database |
| `<timestamp>-files.tar` | Public file attachments |
| `<timestamp>-private-files.tar` | Private file attachments |

### Automating backups

The built-in Frappe scheduler runs `bench backup` automatically on a schedule you control from **Setup → System Settings → Backup Settings** inside your Frappe site. By default Frappe keeps the last three backups.

For off-site backups, copy the files out of the bench path or use Frappe's S3 push settings.

## Restoring a backup

To restore a backup, open a shell into the bench and use the `bench restore` command:

```bash
fm shell mybench
```

Then inside the shell:

```bash
bench --site mybench.localhost restore \
  /workspace/frappe-bench/sites/mybench.localhost/private/backups/<timestamp>-database.sql.gz \
  --with-public-files  /workspace/frappe-bench/sites/mybench.localhost/private/backups/<timestamp>-files.tar \
  --with-private-files /workspace/frappe-bench/sites/mybench.localhost/private/backups/<timestamp>-private-files.tar
```

!!! tip
    You can copy backup files from outside the container into `~/frappe/sites/<benchname>/workspace/frappe-bench/sites/<sitename>/private/backups/` on the host. They appear at the same path inside the container.

## FM migration backups (automatic)

When you run `fm migrate`, FM automatically backs up your bench's configuration files before applying any changes. This is separate from site data backups.

Migration backups are stored at:

```
~/frappe/sites/<benchname>/backups/migrations/<timestamp>/
```

They include, among others:

- `bench_config.toml` - bench configuration
- `docker-compose.yml` - container definitions
- `common_site_config.json` - Frappe site config
- a gzipped SQL dump of the site database (for migrations that touch data)

If a migration fails partway through, FM can restore these files automatically (see the `--on-failure` option of `fm migrate`). You can also restore them manually by copying them back to the bench directory.

### Skipping backup during migration

If a backup is failing (for example because the database is in a bad state) you can skip it:

```bash
# Skip backup for a specific bench only
fm migrate --skip-backup-for mybench

# Skip backup for all benches (dangerous)
fm migrate --all-benches --skip-all-backup
```

!!! danger
    Skipping migration backups means you cannot roll back automatically if something goes wrong. Only do this as a last resort.

## Resetting a bench completely

If you want to wipe a bench's database and start fresh (keeping the app code), use `fm reset`:

```bash
fm reset mybench
```

This drops the database and reinstalls all apps. Back up first - this is irreversible.

---

!!! info "See also"
    - [Deployment](../deploy/index.md#releases-history-and-pruning) - image benches keep a pre-migrate DB dump per release; `fm prune` trims old dumps, and `fm switch --previous --restore-db` rolls back to one
    - [Migrations reference](../reference/migrations.md) - how FM migrations work and how rollbacks are triggered
    - [fm migrate command](../commands/migrate.md) - all migration flags
