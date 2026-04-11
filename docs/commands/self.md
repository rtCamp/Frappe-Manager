## `fm self`

Self commands.

**Usage**:

```console
$ fm self [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `update`: Check for and install frappe-manager updates.
* `update-images`: Pull latest FM stack docker images.
* `compose`: Run docker compose commands with auto-detected compose files.


### `fm self update`

Check for and install frappe-manager updates.

**Usage**:

```console
$ fm self update [OPTIONS]
```

**Options**:

* `-y, --yes`: Skip confirmation prompt and proceed with update


**Examples**:

_Update fm to the latest version available on pypi_
```bash
fm self update
```


### `fm self update-images`

Pull latest FM stack docker images.

**Usage**:

```console
$ fm self update-images
```


**Examples**:

_Update all Frappe docker images to latest versions_
```bash
fm self update-images
```


### `fm self compose`

Run docker compose commands with auto-detected compose files.

Automatically finds and includes all docker-compose*.yml files in the bench directory.

**Usage**:

```console
$ fm self compose
```


**Examples**:

_Show running containers for mybench_
```bash
fm self compose mybench ps
```

_Start containers in detached mode_
```bash
fm self compose mybench up -d
```

_Follow logs for frappe service_
```bash
fm self compose mybench logs -f frappe
```

_Execute bash in frappe container_
```bash
fm self compose mybench exec frappe bash
```

_Restart specific service_
```bash
fm self compose mybench restart frappe
```

_View container resource usage_
```bash
fm self compose mybench stats
```

