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

Updates the installed fm package using the package installer. Use --yes to skip prompts.

**Usage**:

```console
$ fm self update [OPTIONS]
```

**Options**:

* `-y, --yes`: Skip confirmation prompt and proceed with update


**Examples**:

_Update fm to the latest version available on pypi_
Checks PyPI for the latest frappe-manager release and installs it if available.
```bash
fm self update
```

_Update without confirmation prompt_
Skips the interactive confirmation and updates immediately if a new version is found.
```bash
fm self update --yes
```


### `fm self update-images`

Pull latest FM stack docker images.

**Usage**:

```console
$ fm self update-images
```


**Examples**:

_Update all Frappe docker images to latest versions_
Pulls the latest Docker images used by FM to keep runtime images up to date.
```bash
fm self update-images
```

_Update images in verbose mode_
Shows detailed pull progress for each Docker image layer.
```bash
fm self update-images --verbose
```


### `fm self compose`

Run docker compose commands with auto-detected compose files.

Automatically finds and includes all docker-compose*.yml files in the bench directory.

**Usage**:

```console
$ fm self compose
```


**Examples**:

_Show running containers for a bench_
Runs 'docker compose ps' for the bench using all discovered compose files.
```bash
fm self compose mybench ps
```

_Start containers in detached mode_
Starts containers in detached mode using the bench's compose files.
```bash
fm self compose mybench up -d
```

_Follow logs for frappe service_
Runs 'docker compose logs -f frappe' to stream logs for the frappe service.
```bash
fm self compose mybench logs -f frappe
```

_Execute bash in frappe container_
Executes an interactive bash shell in the frappe container.
```bash
fm self compose mybench exec frappe bash
```

_Restart specific service_
Restarts a single service using docker compose for targeted debugging.
```bash
fm self compose mybench restart frappe
```

_View container resource usage_
Runs 'docker compose stats' to view resource usage for bench containers.
```bash
fm self compose mybench stats
```

