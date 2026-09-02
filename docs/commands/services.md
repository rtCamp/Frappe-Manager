## `fm services`

Services commands.

**Usage**:

```console
$ fm services [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `start`: Start the global services shared by every bench.
* `stop`: Stop the global services shared by every bench.
* `restart`: Restart the global services shared by every bench.
* `shell`: Open a bash shell in one of the global service containers.


### `fm services start`

Start the global services shared by every bench.

**Usage**:

```console
$ fm services start SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`  [required]


## Examples

### Bring the global stack up

Services already running are left alone, so this is safe to re-run.

```bash
fm services start all
```

### Start the database only

```bash
fm services start global-db
```


### `fm services stop`

Stop the global services shared by every bench.

Every bench is reached through global-nginx-proxy and keeps its data in global-db, so stopping these leaves the bench containers running but unreachable and without a database.

**Usage**:

```console
$ fm services stop SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`  [required]


## Examples

### Take the global stack down

Services already stopped are left alone.

```bash
fm services stop all
```


### `fm services restart`

Restart the global services shared by every bench.

Every bench is reached through global-nginx-proxy and keeps its data in global-db, so restarting these is a brief outage for every bench on this host. The containers are restarted in place and never recreated, so a newly pulled image or an edited compose file is not picked up.

**Usage**:

```console
$ fm services restart SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`  [required]


## Examples

### Apply a change to the proxy

A restart is what puts a new proxy config into effect, for instance after fm self real-ip.

```bash
fm services restart global-nginx-proxy
```

### Restart the whole global stack

Benches are unreachable until the proxy is back up.

```bash
fm services restart all
```


### `fm services shell`

Open a bash shell in one of the global service containers.

**Usage**:

```console
$ fm services shell SERVICE_NAME [OPTIONS]
```

**Arguments**:

* `SERVICE_NAME`: One service; all is not accepted here.  [required]

**Options**:

* `--user`: Run the shell as this user instead of the container's default.


## Examples

### Open a shell in the global database

```bash
fm services shell global-db
```

### Open a shell in the proxy

```bash
fm services shell global-nginx-proxy
```

