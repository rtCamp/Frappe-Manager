# `fm self`

Manage the fm installation itself: upgrade it, pull images, stop everything, uninstall.

**Usage**:

```console
$ fm self COMMAND [ARGS]...
```

| Command | Description |
|---|---|
| [`fm self upgrade`](#fm-self-upgrade) | Upgrade fm to the latest release published on PyPI. |
| [`fm self update-images`](#fm-self-update-images) | Pull the docker images fm's stack runs on. |
| [`fm self stop`](#fm-self-stop) | Stop every bench on this host, then the global services (nginx-proxy, mariadb). |
| [`fm self uninstall`](#fm-self-uninstall) | Remove everything fm put on this host: benches, shared services, its directory, and its dev CA. |

## `fm self upgrade`

Upgrade fm to the latest release published on PyPI.

An install already ahead of PyPI, such as a dev or pre-release build, is reported as up to date and left alone: fm is never downgraded under benches whose on-disk state a newer fm wrote.

**Usage**:

```console
$ fm self upgrade [OPTIONS]
```

**Options**:

* `-y, --yes`: Upgrade without asking for confirmation.  [default: false]

### Examples

#### Upgrade fm to the latest release

```bash
fm self upgrade
```

#### Upgrade without the confirmation prompt

```bash
fm self upgrade --yes
```

## `fm self update-images`

Pull the docker images fm's stack runs on.

Running containers keep the image they started with until they are recreated.

Which tags get pulled is fixed by the installed fm version, so a newer stack starts with fm self upgrade.

**Usage**:

```console
$ fm self update-images
```

### Examples

#### Pull the images fm's stack runs on

```bash
fm self update-images
```

## `fm self stop`

Stop every bench on this host, then the global services (nginx-proxy, mariadb).

Nothing fm manages is left running unless you narrow the blast radius with --benches-only or --global-only.

A bench that fails to stop does not abort the run: the remaining benches and the global services are still stopped, and fm ends by naming what is still up and exiting non-zero.

**Usage**:

```console
$ fm self stop
```

### Examples

#### Stop everything

```bash
fm self stop
```

#### Stop the global services, leave the benches up

```bash
fm self stop --global-only
```

#### Stop the benches, leave the global services up

```bash
fm self stop --benches-only
```

## `fm self uninstall`

Remove everything fm put on this host: benches, shared services, its directory, and its dev CA.

This destroys every bench and every database in fm's own mariadb, with no undo and no backup taken. A schema on a database server fm does not own is never touched. Narrow the blast radius with --only: 'benches' wipes the benches and leaves the shared services and fm's config, which is the closest thing to a fresh start that keeps the host set up.

Plan-first: every container, path, image and trust store is listed before anything happens, then one confirmation covers it; --dry-run stops after the plan. Public base images (mariadb, redis, nginx-proxy, mailpit, adminer) are never removed, because this host may be using them for something else; --images removes fm's own.

fm's own package is not uninstalled here: the command to do that is printed at the end, because a running process cannot reliably delete the environment it is executing from.

**Usage**:

```console
$ fm self uninstall [OPTIONS]
```

**Options**:

* `--only [benches|services|host|trust]`: Act on this tier only (repeatable): benches, services, host, trust. Default: all four.
* `--images`: Also remove fm's own docker images (ghcr.io/rtcamp/frappe-manager-*).  [default: false]
* `--keep-backups`: Leave ~/frappe/backups on disk.  [default: false]
* `-y, --yes`: Uninstall without asking, including the typed confirmation.  [default: false]
* `--dry-run`: Print the plan and exit without removing anything; never prompts.  [default: false]

### Examples

#### See everything that would be removed, change nothing

```bash
fm self uninstall --dry-run
```

#### Remove every trace of fm from this host

Prints the full plan, then asks for the word 'uninstall' typed back.

```bash
fm self uninstall
```

#### Also remove fm's own docker images

```bash
fm self uninstall --images
```

#### Reset the benches only, keep the shared services and fm's config

```bash
fm self uninstall --only benches
```
