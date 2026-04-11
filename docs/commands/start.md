## `fm start`

Start a bench.

Starts all containers for the specified bench. Use --force to recreate containers.
Various --reconfigure options allow syncing configuration changes without full restart.
Use --reconfigure-supervisor for process management,
and --reconfigure-workers to update worker configurations.

**Usage**:

```console
$ fm start BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `-f, --force`: Recreate containers
* `--reconfigure-supervisor`: Reconfigure supervisor
* `--reconfigure-common-site-config`: Reconfigure site config
* `--reconfigure-workers`: Reconfigure workers
* `--include-default-workers`: Include default workers
* `--include-custom-workers`: Include custom workers
* `--sync-dev-packages`: Sync dev packages


**Examples**:

_Start bench containers_
```bash
fm start mybench
```

_Force recreate containers_
```bash
fm start mybench --force
```

_Start and reconfigure workers_
```bash
fm start mybench --reconfigure-workers
```

_Start with supervisor reconfiguration_
```bash
fm start mybench --reconfigure-supervisor
```

_Start and sync dev packages_
```bash
fm start mybench --sync-dev-packages
```

