## `fm start`

Start a bench.

Starts all containers for the specified bench. Reconfigure flags allow updating process and worker settings.

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


## Examples

### Start bench containers

Starts all containers for the specified bench. Useful to bring a bench up after stopping or system reboot.

```bash
fm start mybench
```

### Force recreate containers

Recreates containers even if they already exist; use when container images or configuration changed.

```bash
fm start mybench --force
```

### Start and reconfigure workers

Starts the bench and reconfigures worker processes to pick up configuration changes.

```bash
fm start mybench --reconfigure-workers
```

### Start with supervisor reconfiguration

Reconfigures the supervisor process manager during start to update process definitions.

```bash
fm start mybench --reconfigure-supervisor
```

### Start and sync dev packages

Synchronizes development packages after starting; useful in development workflows.

```bash
fm start mybench --sync-dev-packages
```

