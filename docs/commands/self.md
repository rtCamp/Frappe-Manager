## `fm self`

Self commands.

**Usage**:

```console
$ fm self [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `upgrade`: Upgrade fm to the latest release published on PyPI.
* `update-images`: Pull the docker images fm's stack runs on.
* `compose`: Run docker compose against a bench with all of its compose files already wired up.
* `stop`: Stop every bench on this host, then the global services (global-nginx-proxy, global-db).


### `fm self upgrade`

Upgrade fm to the latest release published on PyPI.

An install already ahead of PyPI, such as a dev or pre-release build, is reported as up to date and left alone: fm is never downgraded under benches whose on-disk state a newer fm wrote.

**Usage**:

```console
$ fm self upgrade [OPTIONS]
```

**Options**:

* `-y, --yes`: Upgrade without asking for confirmation.


## Examples

### Upgrade fm to the latest release

```bash
fm self upgrade
```

### Upgrade without the confirmation prompt

```bash
fm self upgrade --yes
```


### `fm self update-images`

Pull the docker images fm's stack runs on.

Running containers keep the image they started with until they are recreated.

Which tags get pulled is fixed by the installed fm version, so a newer stack starts with fm self upgrade.

**Usage**:

```console
$ fm self update-images
```


## Examples

### Pull the images fm's stack runs on

```bash
fm self update-images
```


### `fm self compose`

Run docker compose against a bench with all of its compose files already wired up.

Everything after the bench name is handed to docker compose untouched, so any subcommand and flag it accepts works here.

docker compose runs with the bench directory as its working directory, so a relative path in the arguments resolves there and not against the directory you called fm from.

**Usage**:

```console
$ fm self compose
```


## Examples

### Show the bench's containers

```bash
fm self compose mybench ps
```

### Follow the frappe logs

```bash
fm self compose mybench logs -f frappe
```

### Open a shell in a container

```bash
fm self compose mybench exec frappe bash
```

### Restart one service

```bash
fm self compose mybench restart frappe
```


### `fm self stop`

Stop every bench on this host, then the global services (global-nginx-proxy, global-db).

Nothing fm manages is left running unless you narrow the blast radius with --benches-only or --global-only.

A bench that fails to stop does not abort the run: the remaining benches and the global services are still stopped, and fm ends by naming what is still up and exiting non-zero.

**Usage**:

```console
$ fm self stop
```


## Examples

### Stop everything

```bash
fm self stop
```

### Stop the global services, leave the benches up

```bash
fm self stop --global-only
```

### Stop the benches, leave the global services up

```bash
fm self stop --benches-only
```

