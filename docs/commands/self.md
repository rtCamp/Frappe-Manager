## `fm self`

Self commands.

**Usage**:

```console
$ fm self [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `update`: Update fm to the latest release published on PyPI.
* `update-images`: Pull the docker images fm's stack runs on.
* `compose`: Run docker compose against a bench with all of its compose files already wired up.
* `stop`: Stop every bench on this host, then the global services (global-nginx-proxy, global-db).
* `real-ip`: Restore the visitor's real IP at the global nginx proxy when it sits behind a CDN or load balancer.


### `fm self update`

Update fm to the latest release published on PyPI.

An install already ahead of PyPI, such as a dev or pre-release build, is reported as up to date and left alone: fm is never downgraded under benches whose on-disk state a newer fm wrote.

**Usage**:

```console
$ fm self update [OPTIONS]
```

**Options**:

* `-y, --yes`: Update without asking for confirmation.


## Examples

### Update fm to the latest release

```bash
fm self update
```

### Update without the confirmation prompt

```bash
fm self update --yes
```


### `fm self update-images`

Pull the docker images fm's stack runs on.

Running containers keep the image they started with until they are recreated.

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


### `fm self real-ip`

Restore the visitor's real IP at the global nginx proxy when it sits behind a CDN or load balancer.

Trust only the ranges you actually sit behind: whatever you trust fully controls the client IP that fm, your logs and frappe go on to see.

**Usage**:

```console
$ fm self real-ip [OPTIONS]
```

**Options**:

* `--cdn`: Trust a CDN's published ranges. Supported: cloudflare.
* `--trust`: CIDR range or single IP of a proxy in front of fm (repeatable).
* `--header`: Header the client IP is read from. Defaults to CF-Connecting-IP for --cdn cloudflare and X-Forwarded-For otherwise; anything that is not a valid header name is refused.
* `--off`: Remove the configuration and reload the proxy.
* `--status`: Show the active configuration. Writes nothing.


## Examples

### Trust Cloudflare

Proxy logs, fm maintenance --allow-ip and frappe's rate limiting then see the visitor instead of Cloudflare's edge.

```bash
fm self real-ip --cdn cloudflare
```

### Trust your own load balancer

Each run replaces the whole configuration, so pass every range you sit behind in one call.

```bash
fm self real-ip --trust 203.0.113.0/24
```

### Show what is trusted

```bash
fm self real-ip --status
```

