# `fm update`

Change a bench's settings.

Not bench update: app code ships with fm bake then fm switch. Apps are managed with fm apps add, alias domains with fm domain, admin tools with fm tools, APM with fm telemetry.

Most options change the whole bench. --db-ca is the one Site Option below, and a plain fm update BENCH applies it to the bench's primary site; name the site with fm update BENCH/SITE when the bench serves more than one.

The whole update is decided before any of it is applied, so an invalid flag changes nothing and a value that already matches is reported instead of reapplied. --dry-run prints that plan and exits without touching the bench.

**Usage**:

```console
$ fm update BENCH(/SITE) [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE to act on one of its sites. Without a site part, the bench's primary site is used.

**Options**:

* `-e, --environment [prod|dev]`: Switch the bench between dev and prod serving (FRAPPE_ENV), recreating the frappe container. Admin tools and developer mode are left as they are; use 'fm tools enable'/'fm tools disable' or --developer-mode to change those.
* `--developer-mode [enable|disable]`: Toggle frappe developer mode, so DocType edits write to app files.
* `--upload-limit TEXT`: Set the maximum file upload size, e.g. 100M or 1G.
* `--restart-policy [no|always|on-failure|unless-stopped]`: Update Docker restart policy for all bench services.
* `--python TEXT`: Update the Python version (e.g. '3.11', '>=3.11,<3.14'); recreates the venv and reinstalls apps.
* `--node TEXT`: Update the Node version (e.g. '20', '>=18') and set it as the bench default.
* `--skip-version-check`: Accept a Python/Node version that does not satisfy frappe's requirement.  [default: false]
* `--recreate-python-env/--no-recreate-python-env`: Rebuild the venv. Alongside --python it is the default (the new interpreter needs a fresh venv); --no-recreate-python-env installs the new Python and leaves the existing venv in place. On its own, with no version change, it rebuilds the venv at the recorded Python/Node and reinstalls the apps.
* `--drain/--no-drain`: Suspend RQ workers and wait for in-flight jobs before restarting or recreating them, and abort the update if they outlast \[workers].drain_timeout; --no-drain interrupts them instead.  [default: true]
* `--redis-cache TEXT`: Point the bench's framework cache at an external redis, e.g. redis://r.example:6379/0. Independent of the queue: the cache can stay on fm's own container while the queue moves out, or the other way round.
* `--redis-queue TEXT`: Point the bench's queue and realtime at an external redis, e.g. redis://r.example:6379/1. Independent of the cache.
* `--no-redis-cache`: Bring the framework cache back to fm's own per-bench redis container, leaving the queue as it is.  [default: false]
* `--no-redis-queue`: Bring the queue and realtime back to fm's own per-bench redis container, leaving the cache as it is.  [default: false]
* `--abandon-queued`: Switch the redis queue even though jobs are still pending, leaving them on the old server instead of pausing producers and waiting for the backlog to drain. Those jobs are never run.  [default: false]
* `--no-redis`: Bring BOTH sides back to fm's own per-bench redis containers.  [default: false]
* `--db-ca PATH`: Reinstall the external database CA after a rotation: the site PEM, the bench ca-bundle.pem the dumps use, and the recorded path are refreshed together.
* `--dry-run`: Print what would change and exit without touching the bench.  [default: false]

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

### Rebuild a broken venv at the recorded versions

No version change: recreates the venv on the bench's recorded Python/Node and reinstalls all apps. The repair verb for a corrupted or half-installed env/.

```bash
fm update mybench --recreate-python-env
```

## See also

- [App management](../guides/app-management.md)
- [Python and Node versions](../guides/python-node-versions.md)
