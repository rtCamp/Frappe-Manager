## `fm create`

Create a new bench and install apps into it.

Image runtime (--runtime image) refuses --apps, --python, --node and developer mode, which the image already carries; 'fm update BENCH --runtime mount' converts a bench to an editable workspace.

**Usage**:

```console
$ fm create BENCH(/SITE) [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE)`: Bench to create, or BENCH/SITE to add a site to a bench that already exists. A bench name is just a name: 'shop' creates a bench 'shop' serving a site 'shop.localhost', and a name that is already a domain serves that domain.  [required]

**Options**:

* `-a, --apps`: App to install: appname or owner/repo, optional :branch (repeatable). Frappe is always first.
* `-e, --environment`: Bench environment; sets the dev-mode and restart defaults.
* `--developer-mode`: Let DocType edits write app source files. Already on for a dev-environment bench.
* `--bench-only`: Create the bench (config, directory, containers) with no site in it. Sites are added afterwards with 'fm create BENCH/SITE'.
* `--admin-pass`: Administrator password.
* `--alias-domains`: Extra domains this bench answers on (comma-separated). Certificates come from 'fm ssl add'.
* `-t, --github-token`: Token for cloning private app repos.
* `--python`: Python version, e.g. '3.11'. Auto-detected by default.
* `--node`: Node version, e.g. '20'. Auto-detected by default.
* `--restart`: Docker restart policy. Defaults to 'no' (dev) or 'unless-stopped' (prod).
* `--allow-domain-conflicts`: Skip the domain uniqueness check.
* `--runtime`: 'mount' (default) live-mounts an editable workspace; 'image' runs a pre-built app image, moved to a new tag with 'fm switch'.
* `--base-image`: The image the bench's containers run (repo:tag). Mount runtime: the base frappe image, with your editable workspace mounted over it. Image runtime: the pre-built app image itself, which is where the bench starts and which 'fm switch' later moves to another tag.
* `--seed-image`: Mount runtime: seed the workspace from a baked app image (repo:tag) instead of cloning and installing apps. --apps, --python and --node then override what it carries. This is a one-time copy, not what the containers run: see --base-image.
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

--base-image is the image the containers run. Here it is the app image itself, and fm switch moves the bench to later tags from there.

```bash
fm create mybench --runtime image --base-image ghcr.io/acme/mybench:v15-20260822
```

### Seed an editable workspace from a baked image

Copies the image's apps, env and built assets onto the host once, skipping clone and install. The bench still boots on the default base image unless --base-image says otherwise.

```bash
fm create mybench --seed-image ghcr.io/acme/mybench:v15-20260822
```

### Create a bench on an external database

Pass --db-admin-user with --db-admin-password instead of --db-password to have fm create the schema, the user and the grant.

```bash
fm create mybench --db-host db.example.com --db-name app_prod --db-password - --db-ca /etc/ssl/rds-bundle.pem
```

## Related

- [Runtimes: Mount vs Image](../concepts/runtimes.md)
