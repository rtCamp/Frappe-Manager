## `fm start`

Start a bench's containers, admin tools and workers.

**Usage**:

```console
$ fm start BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

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

