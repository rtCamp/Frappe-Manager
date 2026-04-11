## `fm ngrok`

Create ngrok tunnel for bench

**Usage**:

```console
$ fm ngrok BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `-t, --auth-token`: Ngrok authentication token
* `--save-token/--no-save-token`: Save or don't save the ngrok auth token to config for future use


**Examples**:

_Create ngrok tunnel for bench_
```bash
fm ngrok mybench --auth-token YOUR_TOKEN
```

_Use saved auth token from config_
```bash
fm ngrok mybench
```

