## `fm update`

Change a bench's settings and runtime.

Not `bench update`: app code ships with fm bake then fm switch. The bench must be running, and the mount-only options need an editable workspace, so demote an image bench with --runtime mount first.

**Usage**:

```console
$ fm update BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `--admin-tools`: Enable/disable admin tools (Adminer at /adminer, Mailpit at /mailpit).
* `-e, --environment`: Switch the bench between dev and prod serving (FRAPPE_ENV), recreating the frappe container. Admin tools and developer mode are left as they are; use --admin-tools or --developer-mode to change those.
* `--runtime`: Convert the bench runtime. 'mount' extracts an editable workspace from the currently deployed image, stashing anything stale it finds; converting back is a deploy, so use fm switch.
* `-a, --apps`: Replace or add an app on the running bench (repeatable; appname:ref or org/repo:ref). Replaced code is stashed, never deleted; assets rebuild and the site migrates.
* `--developer-mode`: Toggle frappe developer mode, so DocType edits write to app files.
* `--mailpit-as-default-mail-server`: Route the site's outgoing mail to Mailpit. Applies when enabling admin tools.
* `--add-alias`: Add alias domains (comma-separated, e.g. www.example.com,api.example.com).
* `--remove-alias`: Remove alias domains (comma-separated, e.g. shop.example.com).
* `--upload-limit`: Set the maximum file upload size, e.g. 100M or 1G.
* `--python`: Update the Python version (e.g. '3.11', '>=3.11,<3.14'); recreates the venv and reinstalls apps.
* `--node`: Update the Node version (e.g. '20', '>=18') and set it as the bench default.
* `--skip-version-check`: Accept a Python/Node version that does not satisfy frappe's requirement.
* `--recreate-python-env/--no-recreate-python-env`: Recreate the venv when --python changes the interpreter; --no-recreate-python-env installs the new Python and leaves the existing venv in place.
* `--restart`: Update Docker restart policy for all bench services.
* `--allow-domain-conflicts`: Add an alias domain even when another bench already serves it.
* `--newrelic/--no-newrelic`: Enable or disable NewRelic APM monitoring for the web process.
* `--newrelic-license-key`: NewRelic ingest license key. Required the first time you enable NewRelic.
* `--db-ca`: Reinstall the external database CA after a rotation: the site PEM, the bench ca-bundle.pem the dumps use, and the recorded path are refreshed together.


## Examples

### Switch to the production environment

```bash
fm update mybench -e prod
```

### Enable the admin tools

```bash
fm update mybench --admin-tools enable
```

### Turn on developer mode

```bash
fm update mybench --developer-mode enable
```

### Add an alias domain

No certificate is issued for the new domain; run fm ssl add afterwards.

```bash
fm update mybench --add-alias www.example.com
```

### Bump the Python version

```bash
fm update mybench --python 3.11
```

## Related

- [App Management](../guides/app-management.md)
- [Python & Node Versions](../guides/python-node-versions.md)
