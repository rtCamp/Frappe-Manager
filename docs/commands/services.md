## `fm services`

Services commands.

**Usage**:

```console
$ fm services [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `start`: Starts global services.
* `stop`: Stops global services.
* `restart`: Restarts global services.
* `shell`: Open shell for the specificed global service.


### `fm services start`

Starts global services.

**Usage**:

```console
$ fm services start SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`: Name of the service.  [required]


## Examples

### Start global-db only

Starts only the global-db service used to store bench databases.

```bash
fm services start global-db
```

### Start all global services

Starts all global services managed by FM (nginx-proxy, global-db, etc.).

```bash
fm services start all
```


### `fm services stop`

Stops global services.

**Usage**:

```console
$ fm services stop SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`: Name of the service.  [required]


## Examples

### Stop global-db

Stops the global-db service. Use when maintaining or backing up the global database.

```bash
fm services stop global-db
```

### Stop all services

Stops all global services managed by FM.

```bash
fm services stop all
```


### `fm services restart`

Restarts global services.

**Usage**:

```console
$ fm services restart SERVICE_NAME
```

**Arguments**:

* `SERVICE_NAME`: Name of the service.  [required]


## Examples

### Restart global-db only

Restarts the global-db service only.

```bash
fm services restart global-db
```

### Restart all global services

Restarts all managed global services.

```bash
fm services restart all
```


### `fm services shell`

Open shell for the specificed global service.

**Usage**:

```console
$ fm services shell SERVICE_NAME [OPTIONS]
```

**Arguments**:

* `SERVICE_NAME`: Name of the service.  [required]

**Options**:

* `--user`: Connect as this user.


## Examples

### Shell global-db

Opens a shell into the global-db service for maintenance tasks.

```bash
fm services shell global-db
```

### Shell global-nginx-proxy

Opens a shell into the nginx proxy container used for routing bench domains.

```bash
fm services shell global-nginx-proxy
```

