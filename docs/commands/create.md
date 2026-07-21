## `fm create`

Create a new bench with apps.

Creates a bench directory, config, and installs requested apps. If not specified, Frappe is included by default.

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
* `-t, --github-token`: GitHub token for private repos (or use GITHUB_TOKEN env var)
* `--python`: Python version (e.g., '3.11'). Auto-detected by default.
* `--node`: Node version (e.g., '18', '20'). Auto-detected by default.
* `--restart`: Docker restart policy. Defaults to 'no' (dev) or 'unless-stopped' (prod).
* `--allow-domain-conflicts`: Skip domain uniqueness validation (not recommended). Allows creating benches with duplicate domains.
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

