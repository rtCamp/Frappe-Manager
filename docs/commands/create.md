## `fm create`

Create a new bench and install apps into it.

Image runtime (--runtime image) refuses --apps, --python, --node and developer mode, which the image already carries; 'fm update BENCHNAME --runtime mount' converts a bench to an editable workspace.

**Usage**:

```console
$ fm create BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Bench name, also its domain. A bare name becomes mybench.localhost.  [required]

**Options**:

* `-a, --apps`: App to install: appname or owner/repo, optional :branch (repeatable). Frappe is always first.
* `-e, --environment`: Bench environment; sets the dev-mode and restart defaults.
* `--developer-mode`: Let DocType edits write app source files. Already on for a dev-environment bench.
* `--template`: Create the bench config and directory only, no site.
* `--admin-pass`: Administrator password.
* `--alias-domains`: Extra domains this bench answers on (comma-separated). Certificates come from 'fm ssl add'.
* `-t, --github-token`: Token for cloning private app repos.
* `--python`: Python version, e.g. '3.11'. Auto-detected by default.
* `--node`: Node version, e.g. '20'. Auto-detected by default.
* `--restart`: Docker restart policy. Defaults to 'no' (dev) or 'unless-stopped' (prod).
* `--allow-domain-conflicts`: Skip the domain uniqueness check.
* `--runtime`: 'mount' (default) live-mounts an editable workspace; 'image' runs a pre-built app image, moved to a new tag with 'fm switch'.
* `--image`: Mount runtime: base frappe image (repo:tag). Image runtime: the app image to run, local or pullable.
* `--from-image`: Seed the workspace from a baked app image (repo:tag) instead of cloning and installing apps. --apps, --python and --node then override what it carries.
* `--config`: TOML base config: file path or inline. Explicit flags win; later --config wins.
* `--newrelic/--no-newrelic`: Enable NewRelic APM for the web process.
* `--newrelic-license-key`: NewRelic ingest license key. Required with --newrelic.
* `--db-host`: External MariaDB host, replacing fm's global-db container. MySQL is not a supported backend.
* `--db-port`: Port of the external database server.
* `--db-name`: Schema on that server this site lives in. Required with --db-host.
* `--db-user`: Login user for the schema. Defaults to the schema name, and must equal it on a v15 bench.
* `--db-password`: Password of the site's database login. Pass - for stdin; omit with --db-admin-user to generate one.
* `--db-admin-user`: Administrative login, used once at create time to create the schema, the site user and the grant. Never stored.
* `--db-admin-password`: Password for --db-admin-user. Pass - to read it from stdin.
* `--db-ca`: Host path to the CA bundle signing the server certificate. Required whenever the server enforces TLS.
* `--db-no-verify-hostname`: Check the certificate chain but not that the certificate names the host dialled.
* `--attach-existing-site`: The schema already holds a Frappe site: build the bench around it and write nothing to the database.
* `--encryption-key`: The attached site's encryption_key, - to read from stdin. Without it Frappe mints a new one and existing encrypted secrets stop being readable.
* `--redis-cache`: External redis URL for the framework cache, e.g. redis://r.example:6379/0. Requires --redis-queue.
* `--redis-queue`: External redis URL for the queue and realtime. Use a different logical index from --redis-cache: a restore mass-deletes the cache index.


## Examples

### Create a bench with Frappe only

```bash
fm create mybench
```

### Add apps, pinned to a branch or not

```bash
fm create mybench --apps erpnext:version-15 --apps hrms
```

### Create a production bench

```bash
fm create mybench -e prod --apps erpnext
```

### Run a pre-built app image

```bash
fm create mybench --runtime image --image ghcr.io/acme/mybench:v15-20260822
```

### Create a bench on an external database

Pass --db-admin-user with --db-admin-password instead of --db-password to have fm create the schema, the user and the grant.

```bash
fm create mybench --db-host db.example.com --db-name app_prod --db-password - --db-ca /etc/ssl/rds-bundle.pem
```

## Related

- [Runtimes: Mount vs Image](../concepts/runtimes.md)
