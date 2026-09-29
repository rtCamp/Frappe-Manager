# `fm create`

Create a new bench, or add a site to one that already exists.

fm create shop makes the bench shop serving the site shop.localhost. fm create shop/b.example.com adds b.example.com to the bench shop, leaving the sites already there untouched, and --bench-only makes an empty bench with no site at all. A bench name is just a name: it does not have to be a domain, and the site it serves is a separate thing from it.

Which half of the address a flag acts on is written on its help panel: Bench Options apply either way, Site Options describe the site being created and are refused when there is no site to apply them to.

Image runtime (--runtime image) refuses --apps, --python, --node and developer mode, which the image already carries.

**Usage**:

```console
$ fm create BENCH(/SITE) [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE)`: Bench to create, or BENCH/SITE to add a site to a bench that already exists. The rule is the dot: 'shop' has none, so it serves 'shop.localhost' and works out of the box, while any name containing one is taken as a domain and served as typed, resolving only where you point it.  [required]

**Options**:

* `-e, --environment [prod|dev]`: Bench environment; sets the dev-mode and restart defaults.  [default: dev]
* `-a, --apps TEXT`: App to install: appname or owner/repo, optional :branch (repeatable). Frappe is always first.
* `--developer-mode [enable|disable]`: Let DocType edits write app source files. Already on for a dev-environment bench.  [default: disable]
* `--bench-only`: Create the bench (config, directory, workspace or image, and containers) with no site in it. 'fm create BENCH/SITE' adds a site afterwards, into the workspace and containers already there. Every Site Option is ignored: there is no site yet for them to describe.  [default: false]
* `--remove-on-failure`: On failure, skip the removal prompt and remove the bench directory and its containers, interactively or not, instead of asking (interactive) or declining and reporting (non-interactive). The command still exits non-zero either way: this cleans up, it does not turn the failure into success. Never drops a schema on an external database (--db-host); that stays declined whether this is passed or not.  [default: false]
* `--dry-run`: Print the bench_config.toml this invocation would write, after --config and the flags are merged, and exit without creating anything.  [default: false]
* `-t, --github-token TEXT`: Token for cloning private app repos.
* `--python TEXT`: Python version, e.g. '3.11'. Auto-detected by default.
* `--node TEXT`: Node version, e.g. '20'. Auto-detected by default.
* `--restart-policy [no|always|on-failure|unless-stopped]`: Docker restart policy. Defaults to 'no' (dev) or 'unless-stopped' (prod).
* `--runtime [mount|image]`: 'mount' (default) live-mounts an editable workspace; 'image' runs a pre-built app image, moved to a new image with 'fm switch'.
* `--app-image TEXT`: The image the bench's containers run, as an image reference (a repository plus a version, e.g. ghcr.io/acme/mybench:v15.2.1). Mount runtime: the base frappe image, with your editable workspace mounted over it. Image runtime: the pre-built app image itself, which is where the bench starts and which 'fm switch' later moves to another image.
* `--nginx-image TEXT`: Image runtime: the companion assets image that serves this app image's static files. Recorded beside it, never worked out from its name. Omitted, fm reads the 'fm.nginx.image' label the bake stamped on the app image.
* `--apps-from TEXT`: Mount runtime: take the apps already built inside a baked image instead of cloning and installing them, named by an image reference. --apps, --python and --node then override what it carries. This is a one-time copy read at create, not an image the bench runs: see --app-image.
* `--config TEXT`: TOML base config: file path or inline. Explicit flags win; later --config wins.
* `--redis-cache TEXT`: External redis URL for the framework cache, e.g. redis://r.example:6379/0. Independent of the queue: either side may stay on fm's own container.
* `--redis-queue TEXT`: External redis URL for the queue and realtime. Use a different logical index from --redis-cache: a restore mass-deletes the cache index.
* `--admin-pass TEXT`: Administrator password for sites created on this bench.  [default: admin]
* `--allow-domain-conflicts`: Skip the domain uniqueness check.  [default: false]
* `--alias-domains TEXT`: Extra domains THIS SITE answers on (comma-separated). Certificates come from 'fm ssl add'.
* `--db-type [mariadb|postgres]`: Database engine for this site: mariadb or postgres. Without --db-host it is fm's own server for that engine.  [default: mariadb]
* `--db-host TEXT`: External database host, replacing fm's mariadb container. MySQL is not a supported backend.
* `--db-port INTEGER`: Port of the external database server. Defaults to the engine's own: 3306 or 5432.  [default: 3306]
* `--db-name TEXT`: Schema on that server this site lives in. Required with --db-host.
* `--db-user TEXT`: Login user for the schema. Defaults to the schema name, and must equal it on a v15 bench.
* `--db-password TEXT`: Password of the site's database login. Pass - for stdin; omit with --db-admin-user to generate one.
* `--db-admin-user TEXT`: Administrative login, used once at create time to create the schema, the site user and the grant. Never stored.
* `--db-admin-password TEXT`: Password for --db-admin-user. Pass - to read it from stdin.
* `--db-ca PATH`: Host path to the CA bundle signing the server certificate. Required whenever the server enforces TLS.
* `--db-no-verify-hostname`: Check the certificate chain but not that the certificate names the host dialled.  [default: false]
* `--attach-existing-site`: The schema already holds a Frappe site: build the bench around it and write nothing to the database.  [default: false]
* `--encryption-key TEXT`: The attached site's encryption_key, - to read from stdin. Without it Frappe mints a new one and existing encrypted secrets stop being readable.

## Examples

### Create a bench with Frappe only

```bash
fm create mybench
```

### Add a site to a bench that already exists

The bench and its other sites are untouched; only b.example.com is created.

```bash
fm create mybench/b.example.com
```

### Add apps, pinned to a branch or not

```bash
fm create mybench --apps erpnext:version-16 --apps hrms
```

### Create a production bench

```bash
fm create mybench -e prod --apps erpnext
```

### Run a pre-built app image

--app-image is the image the containers run; fm switch moves the bench to later images from there. Its companion is read from the image's own fm.nginx.image label, or named with --nginx-image.

```bash
fm create mybench --runtime image --app-image ghcr.io/acme/mybench:v15-20260822
```

### Take apps from a baked image instead of cloning them

Copies that image's apps, env and built assets onto the host once, skipping clone and install. The bench still boots on the default base image unless --app-image says otherwise.

```bash
fm create mybench --apps-from ghcr.io/acme/mybench:v15-20260822
```

### Create a bench on an external database

Pass --db-admin-user with --db-admin-password instead of --db-password to have fm create the schema, the user and the grant.

```bash
fm create mybench --db-host db.example.com --db-name app_prod --db-password - --db-ca /etc/ssl/rds-bundle.pem
```

### Clean up automatically in CI

Pair with fm's own global -n: fm -n create mybench --apps erpnext --remove-on-failure removes the bench and its containers on failure instead of leaving them, and still exits non-zero either way.

```bash
fm create mybench --apps erpnext --remove-on-failure
```

## See also

- [Runtimes: mount vs image](../concepts/runtimes.md)
