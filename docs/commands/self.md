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
* `real-ip`: Restore real client IPs at the global nginx proxy when it sits behind a CDN or load balancer.


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

Stops all running benches and global services (global-db, global-nginx-proxy). Use --global-only or --benches-only to stop only a subset.

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


### `fm self real-ip`

Restore real client IPs at the global nginx proxy when it sits behind a CDN or load balancer.

Without this, everything behind a CDN appears to come from the CDN's edge IPs: proxy logs, fm maintenance --allow-ip, and frappe's per-IP rate limiting all see the edge instead of the visitor. This writes an nginx real_ip configuration trusting exactly the given ranges and reloads the proxy without downtime.

Only trust ranges you actually sit behind: any trusted source fully controls the client IP you observe. The bench-level half (bench nginx trusting fm's internal frontend network) is automatic and needs no command.

**Usage**:

```console
$ fm self real-ip [OPTIONS]
```

**Options**:

* `--cdn`: Trust a known CDN's published ranges. Supported: cloudflare (uses the CF-Connecting-IP header).
* `--trust`: CIDR range (or single IP) of a proxy/LB in front of fm to trust (repeatable). Client IP is taken from X-Forwarded-For.
* `--header`: Override the header the client IP is restored from (default: CF-Connecting-IP for --cdn cloudflare, X-Forwarded-For otherwise).
* `--off`: Remove the real-ip configuration from the global proxy.
* `--status`: Show the active real-ip configuration without changing anything.


## Examples

### Trust Cloudflare

The global proxy restores the visitor's real IP from CF-Connecting-IP for requests arriving from Cloudflare's published ranges (fetched live, vendored fallback). Logs, fm maintenance --allow-ip, and frappe rate limiting then see real client IPs.

```bash
fm self real-ip --cdn cloudflare
```

### Trust a custom load balancer

Restores the client IP from X-Forwarded-For for requests arriving from the given ranges (repeatable). Only list proxies you control: a trusted source controls the IP you see.

```bash
fm self real-ip --trust 203.0.113.0/24
```

### Show or remove the configuration

Shows the active real-ip configuration; --off removes it and reloads the proxy without downtime.

```bash
fm self real-ip --status
```

