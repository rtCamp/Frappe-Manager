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
* `stop`: Stop everything managed by FM.


### `fm self update`

Check for and install frappe-manager updates.

Updates the installed fm package using the package installer. Use --yes to skip prompts.

**Usage**:

```console
$ fm self update [OPTIONS]
```

**Options**:

* `-y, --yes`: Skip confirmation prompt and proceed with update


## Examples

### Update fm to the latest version available on pypi

Checks PyPI for the latest frappe-manager release and installs it if available.

```bash
fm self update
```

### Update without confirmation prompt

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


## Examples

### Update all Frappe docker images to latest versions

Pulls the latest Docker images used by FM to keep runtime images up to date.

```bash
fm self update-images
```

### Update images in verbose mode

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


## Examples

### Show running containers for a bench

Runs 'docker compose ps' for the bench using all discovered compose files.

```bash
fm self compose mybench ps
```

### Start containers in detached mode

Starts containers in detached mode using the bench's compose files.

```bash
fm self compose mybench up -d
```

### Follow logs for frappe service

Runs 'docker compose logs -f frappe' to stream logs for the frappe service.

```bash
fm self compose mybench logs -f frappe
```

### Execute bash in frappe container

Executes an interactive bash shell in the frappe container.

```bash
fm self compose mybench exec frappe bash
```

### Restart specific service

Restarts a single service using docker compose for targeted debugging.

```bash
fm self compose mybench restart frappe
```

### View container resource usage

Runs 'docker compose stats' to view resource usage for bench containers.

```bash
fm self compose mybench stats
```


### `fm self stop`

Stop everything managed by FM.

Stops all running benches and global services (global-db, global-nginx-proxy).
Use --global-only or --benches-only to stop only a subset.

**Usage**:

```console
$ fm self stop
```


## Examples

### Stop everything (all benches + global services)

Stops all running benches and all global services (global-db, global-nginx-proxy).

```bash
fm self stop
```

### Stop only global services

Stops only global services, leaves benches running.

```bash
fm self stop --global-only
```

### Stop only benches

Stops only benches, leaves global services running.

```bash
fm self stop --benches-only
```

