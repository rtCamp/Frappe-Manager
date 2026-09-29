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
| [`fm self uninstall`](#fm-self-uninstall) | Remove everything fm put on this host: benches, shared services, its own files, and its dev CA. |

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

Remove everything fm put on this host: benches, shared services, its own files, and its dev CA.

This destroys every bench and every database in fm's own mariadb and postgres, with no undo and no backup taken. A schema on a database server fm does not own is never touched, and neither is any data outside the four tiers below.

What each --only tier owns, by where the state actually lives (paths below assume fm's default home; FRAPPE_MANAGER_HOME moves all of it, and the plan always prints resolved paths):
- benches   ~/frappe/sites/<bench>, its containers, network and volumes
- services  ~/frappe/services, the shared db/proxy containers and volumes
- host      the rest of ~/frappe, ~/.cache/fm, and every image fm pulled or built
- trust     fm's dev root CA, in the OS and browser trust stores

So 'host' is fm's own files (fm_config.toml, logs, locks, backups) plus the cache directory outside ~/frappe that a hand-rolled cleanup always misses, and its images, which belong to the machine rather than to any one bench. And 'trust' is the tier that outlives 'rm -rf ~/frappe': a root CA whose private key sat in a deleted directory stays trusted by your browser forever. That tier is also why this command needs neither docker nor a config fm can still parse.

Plan-first: every object in scope is listed with its size before anything happens, then one typed confirmation covers all of it, and --dry-run stops after the plan. Everything fm made is included by default, its images with it; --keep-images and --keep-backups opt back out. The image set is read from fm's own compose templates and from the containers being removed, not guessed from a name, so mariadb, redis, nginx-proxy, mailpit, adminer and postgres go too -- except any image some other container on this host still runs, which is kept and named in the plan.

fm's own package is not uninstalled here: the command to do that is printed at the end, because a running process cannot reliably delete the environment it is executing from.

**Usage**:

```console
$ fm self uninstall [OPTIONS]
```

**Options**:

* `--only [benches|services|host|trust]`: Act on these tiers only (repeatable): benches, services, host, trust. Default: all four.
* `--keep-images`: Leave every docker image fm pulled or built on disk (fm's own and the stock mariadb/postgres/redis/nginx-proxy/mailpit/adminer set).  [default: false]
* `--keep-backups`: Leave the backups directory in fm's home (~/frappe/backups by default) on disk.  [default: false]
* `-y, --yes`: Uninstall without asking, including the typed confirmation.  [default: false]
* `--dry-run`: Print the plan and exit without removing anything; never prompts.  [default: false]

### Examples

#### See everything that would be removed, change nothing

The plan names every bench, container, network, volume, path, image and trust store, with sizes. Read it before running the real thing.

```bash
fm self uninstall --dry-run
```

#### Remove every trace of fm from this host

Prints the same plan, then asks for the word 'uninstall' typed back.

```bash
fm self uninstall
```

#### Start over with a clean slate, keep the host set up

Destroys every bench and its databases; the shared services, fm's config and its images stay, so the next 'fm create' does not re-pull or re-provision anything.

```bash
fm self uninstall --only benches
```

#### Remove fm but keep the images for a reinstall

Keeps fm's images and the stock mariadb/postgres/redis/proxy set, so reinstalling costs no pull, and the backups directory survives to be restored into the new install.

```bash
fm self uninstall --keep-images --keep-backups
```

#### Untrust the dev CA on a host where ~/frappe was already deleted by hand

A root CA outlives the directory its key lived in: deleting ~/frappe leaves your browsers and keychain trusting a CA nobody controls any more. Needs neither docker nor a readable fm config.

```bash
fm self uninstall --only trust
```
