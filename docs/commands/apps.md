# `fm apps`

Manage the apps installed on a bench.

**Usage**:

```console
$ fm apps COMMAND [ARGS]...
```

| Command | Description |
|---|---|
| [`fm apps add`](#fm-apps-add) | Fetch app code onto a bench and install it into its site(s). |
| [`fm apps list`](#fm-apps-list) | List the apps a bench has recorded, and what is actually on disk. |

## `fm apps add`

Fetch app code onto a bench and install it into its site(s).

Fetching an app's code and installing it into a site's database are different things. A plain fm apps add BENCH fetches the code and records the app on the bench, so any site created afterwards gets it, and installs it into nothing. fm apps add BENCH/SITE installs and migrates that one site, and fm apps add BENCH/all does every site the bench serves, reporting failures per site and exiting non-zero without stopping at the first.

An image-runtime bench ships app changes by baking a new image: fm bake BENCH --apps APP:REF, then fm switch.

**Usage**:

```console
$ fm apps add BENCH(/SITE|all) APP:REF... [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE|all)`: Bench, BENCH/SITE for one of its sites, or BENCH/all for every site it serves.
* `APP:REF...`: Apps to fetch and install (repeatable; appname:ref or org/repo:ref, or org/repo:ref#subdir for a monorepo app). Replaced code is stashed, never deleted.

**Options**:

* `--drain/--no-drain`: Suspend RQ workers and wait for in-flight jobs before migrating; --no-drain interrupts them instead.  [default: true]

### Examples

#### Fetch an app's code onto the bench

Records the app on the bench and installs it into nothing; any site created afterwards picks it up. Install it into an existing site with 'fm apps add mybench/SITE ...' or 'fm apps add mybench/all ...'.

```bash
fm apps add mybench erpnext:version-15
```

#### Install into one site

Fetches the code, installs it into SITE, then runs bench migrate and restarts.

```bash
fm apps add mybench/SITE erpnext:version-15
```

#### Install into every site the bench serves

A site that fails to install or migrate is reported and the rest still run; the command exits non-zero if any site failed.

```bash
fm apps add mybench/all hrms:version-15
```

#### Install without draining in-flight RQ jobs

Interrupted jobs land in the failed-jobs registry (SIGUSR1, force-stop after [workers].kill_timeout).

```bash
fm apps add mybench/all erpnext:version-15 --no-drain
```

## `fm apps list`

List the apps a bench has recorded, and what is actually on disk.

Recorded apps come from bench_config.toml, with their pinned refs. Installed apps come from the workspace's sites/apps.txt, which an image-runtime bench has no workspace to read -- that section is left out instead of guessed at.

**Usage**:

```console
$ fm apps list BENCH
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

### Examples

#### List a bench's apps

```bash
fm apps list mybench
```
