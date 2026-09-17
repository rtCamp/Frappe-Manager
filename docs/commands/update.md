## `fm update`

Change a bench's settings.

Not bench update: app code ships with fm bake then fm switch. Apps are managed with fm apps add, alias domains with fm domain, admin tools with fm tools, APM with fm telemetry. --runtime mount demotes an image bench to an editable workspace, extracted from the currently deployed image; converting the other direction runs through fm switch instead.

Most options change the whole bench. --db-ca is the one Site Option below, and a plain fm update BENCH applies it to the bench's primary site; name the site with fm update BENCH/SITE when the bench serves more than one.

The whole update is decided before any of it is applied, so an invalid flag changes nothing and a value that already matches is reported instead of reapplied. --dry-run prints that plan and exits without touching the bench.

**Usage**:

```console
$ fm update BENCH(/SITE) [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE to act on one of its sites. Without a site part, the bench's primary site is used.

**Options**:

* `-e, --environment`: Switch the bench between dev and prod serving (FRAPPE_ENV), recreating the frappe container. Admin tools and developer mode are left as they are; use 'fm tools enable'/'fm tools disable' or --developer-mode to change those.
* `--runtime`: Convert the bench's runtime: 'mount' demotes an image bench to an editable workspace extracted from the currently deployed image (no migrate -- code on disk already equals what is running). 'image' is a no-op confirmation on an already-image bench; converting mount -> image runs through 'fm switch' instead, since that migrates the site onto a baked image.
* `--developer-mode`: Toggle frappe developer mode, so DocType edits write to app files.
* `--upload-limit`: Set the maximum file upload size, e.g. 100M or 1G.
* `--restart-policy`: Update Docker restart policy for all bench services.
* `--python`: Update the Python version (e.g. '3.11', '>=3.11,<3.14'); recreates the venv and reinstalls apps.
* `--node`: Update the Node version (e.g. '20', '>=18') and set it as the bench default.
* `--skip-version-check`: Accept a Python/Node version that does not satisfy frappe's requirement.
* `--recreate-python-env/--no-recreate-python-env`: Rebuild the venv. Alongside --python it is the default (the new interpreter needs a fresh venv); --no-recreate-python-env installs the new Python and leaves the existing venv in place. On its own, with no version change, it rebuilds the venv at the recorded Python/Node and reinstalls the apps.
* `--drain/--no-drain`: Suspend RQ workers and wait for in-flight jobs before restarting or recreating them, and abort the update if they outlast \[workers].drain_timeout; --no-drain interrupts them instead.
* `--db-ca`: Reinstall the external database CA after a rotation: the site PEM, the bench ca-bundle.pem the dumps use, and the recorded path are refreshed together.
* `--dry-run`: Print what would change and exit without touching the bench.


## Examples

### Switch to the production environment

```bash
fm update mybench -e prod
```

### Turn on developer mode

```bash
fm update mybench --developer-mode enable
```

### Bump the Python version

```bash
fm update mybench --python 3.11
```

### Raise the upload size limit

```bash
fm update mybench --upload-limit 500M
```

### Demote an image bench to an editable workspace

Extracts the workspace from the currently deployed image; converting back to image runtime runs through fm switch instead.

```bash
fm update mybench --runtime mount
```

## Related

- [App Management](../guides/app-management.md)
- [Python & Node Versions](../guides/python-node-versions.md)
