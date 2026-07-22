## `fm create`

Create a new bench with apps.

Creates a bench directory, config, and installs requested apps. If not specified, Frappe is included by default.

Runtime (--runtime): 'mount' (default) live-mounts code for local
development, and --image overrides the base frappe image. 'image' runs a pre-built
app image (built by `fm bake` or otherwise present/pullable) given via --image and
does not accept --apps/--python/--node -- those are baked into the image.

--config supplies a TOML base (file or inline) for the bench config (e.g. [deploy],
[registry], [remote], [build], hooks); explicit CLI flags override it. Repeatable,
later --config wins.

**Usage**:

```console
$ fm create BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Bench name  [required]

**Options**:

* `-a, --apps`: Apps to install. Format: appname:branch or appname (e.g., erpnext:version-15)
* `-e, --environment`: Environment type (dev or prod)
* `--developer-mode`: Enable/disable developer mode
* `--template`: Create as template bench
* `--admin-pass`: Administrator password
* `--alias-domains`: Alias domains (comma-separated). Use 'fm ssl add' for SSL.
* `-t, --github-token`: Mount runtime only: GitHub token for cloning private app repos (or use GITHUB_TOKEN env var).
* `--python`: Python version (e.g., '3.11'). Auto-detected by default.
* `--node`: Node version (e.g., '18', '20'). Auto-detected by default.
* `--restart`: Docker restart policy. Defaults to 'no' (dev) or 'unless-stopped' (prod).
* `--allow-domain-conflicts`: Skip domain uniqueness validation (not recommended). Allows creating benches with duplicate domains.
* `--runtime`: Runtime: 'mount' (default, live-mounted code) or 'image' (immutable pre-built app image). Default 'mount'.
* `--image`: Mount mode: override the base frappe image (repo:tag). Image mode: the pre-built app image to run (repo:tag; must exist locally or be pullable).
* `--config`: TOML config overlay: a file path or inline TOML content used as the base bench config. Explicit CLI flags override it; repeatable, later --config wins.
* `--newrelic/--no-newrelic`: Enable NewRelic APM monitoring for the web process.
* `--newrelic-license-key`: NewRelic ingest license key. Required when --newrelic is set.


## Examples

### Create bench with Frappe only

Creates a new bench with Frappe installed using the default stable branch. Useful for starting a minimal development environment.

```bash
fm create mybench
```

### Create bench with ERPNext and HRMS

Creates a new bench and installs ERPNext and HRMS on top of Frappe. Useful when you need these apps together.

```bash
fm create mybench --apps erpnext --apps hrms
```

### Create production bench

Creates a production-ready bench with production defaults (no developer tools). Use this for deployment environments.

```bash
fm create mybench -e prod
```

### Create bench with specific branch

Creates a bench installing ERPNext from a specific branch or tag. Use when you need a particular release.

```bash
fm create mybench --apps erpnext:version-14
```

### Create bench with a private app

Installs a private GitHub repository by supplying a token. Keep tokens secret and prefer environment variables.

```bash
fm create mybench --apps myorg/private-app --github-token ghp_xxx
```

### Create bench with custom Python/Node versions

Selects custom Python and Node.js versions for the bench rather than auto-detected defaults.

```bash
fm create mybench --python 3.11 --node 20
```

### Create bench with alias domains

Adds alias domains to the bench configuration. Use 'fm ssl add' to provision certificates for these domains.

```bash
fm create mybench --alias-domains www.example.com,api.example.com
```

