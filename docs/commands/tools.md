## `fm tools`

Tools commands.

**Usage**:

```console
$ fm tools [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `enable`: Start the admin tools (Adminer at /adminer, Mailpit at /mailpit), or route a site to them.
* `disable`: Stop the admin tools (Adminer at /adminer, Mailpit at /mailpit), or unroute a site from them.
* `status`: Report whether admin tools are configured, whether they are enabled, and which sites route to them.


### `fm tools enable`

Start the admin tools (Adminer at /adminer, Mailpit at /mailpit), or route a site to them.

BENCH starts the one container pair the bench has, seeding its compose file on first use and minting its htpasswd. BENCH/SITE only adds the routes for that site's hostnames, leaving the containers as they were; BENCH/all restores the routes for every site the bench serves.

**Usage**:

```console
$ fm tools enable BENCH(/SITE|all) [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE|all)`: Bench, BENCH/SITE for one of its sites, or BENCH/all for every site it serves.

**Options**:

* `--mailpit-as-default-mail-server`: Route outgoing mail to Mailpit for every site the bench holds.


## Examples

### Start the admin tools containers for a bench

Seeds the compose file on first use and mints the tools' htpasswd.

```bash
fm tools enable mybench
```

### Also make Mailpit the bench's default outgoing mail server

```bash
fm tools enable mybench --mailpit-as-default-mail-server
```

### Route one site's hostnames to the already-running tools

The bench's other sites and their existing routes are untouched.

```bash
fm tools enable mybench/site1.localhost
```

### Restore every opted-out site's route at once

Fans the route out over every site the bench serves; the containers were already running.

```bash
fm tools enable mybench/all
```


### `fm tools disable`

Stop the admin tools (Adminer at /adminer, Mailpit at /mailpit), or unroute a site from them.

BENCH stops the one container pair the bench has. BENCH/SITE only drops the routes for that site's hostnames, leaving the tools running for the bench's other sites; BENCH/all drops every site's route without stopping the containers.

**Usage**:

```console
$ fm tools disable BENCH(/SITE|all)
```

**Arguments**:

* `BENCH(/SITE|all)`: Bench, BENCH/SITE for one of its sites, or BENCH/all for every site it serves.


## Examples

### Stop the admin tools containers for a bench

```bash
fm tools disable mybench
```

### Unroute one site, leaving the containers running for the rest

```bash
fm tools disable mybench/site1.localhost
```

### Unroute every site the bench serves

The Adminer and Mailpit containers keep running; stop them with a bare 'fm tools disable BENCH'.

```bash
fm tools disable mybench/all
```


### `fm tools status`

Report whether admin tools are configured, whether they are enabled, and which sites route to them.

**Usage**:

```console
$ fm tools status BENCH
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.


## Examples

### Show admin tools state for a bench

```bash
fm tools status mybench
```

