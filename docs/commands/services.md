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


**Examples**:

_Start global-db only_
```bash
fm services start global-db
```

_Start all global services_
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


**Examples**:

_Stop global-db_
```bash
fm services stop global-db
```

_Stop all services_
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


**Examples**:

_Restart global-db only_
```bash
fm services restart global-db
```

_Restart all global services_
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


**Examples**:

_Shell global-db_
```bash
fm services shell global-db
```

_Shell global-nginx-proxy_
```bash
fm services shell global-nginx-proxy
```

