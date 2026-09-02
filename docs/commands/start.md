## `fm start`

Start a bench's containers, admin tools and workers.

Every start refreshes the bench's own nginx config and its entry in the global proxy, so a bench that predates a change fm makes there heals by being started. It does NOT rewrite the supervisord config, common_site_config.json or the workers compose file: those are only regenerated when you ask, with --reconfigure-supervisor, --reconfigure-common-site-config or --reconfigure-workers, so an edit to bench_config.toml that touches them needs one of those flags or an fm update.

**Usage**:

```console
$ fm start BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

**Options**:

* `-f, --force`: Recreate the containers instead of reusing the existing ones.
* `--reconfigure-supervisor`: Regenerate the supervisord config before starting processes.
* `--reconfigure-common-site-config`: Rewrite common_site_config.json with fm's defaults.
* `--reconfigure-workers`: Regenerate the workers compose file from the bench config.
* `--include-default-workers`: Include the default workers when regenerating. Needs --reconfigure-workers.
* `--include-custom-workers`: Include custom workers when regenerating. Needs --reconfigure-workers.
* `--sync-dev-packages`: Install dev packages on a dev bench, remove them on a prod one.


## Examples

### Start a bench

```bash
fm start mybench
```

### Recreate the containers

Use after an image or compose change.

```bash
fm start mybench --force
```

### Pick up worker config changes

```bash
fm start mybench --reconfigure-workers
```

