## `fm ngrok`

Create ngrok tunnel for bench.

Provisions a public URL for local benches using ngrok; requires an auth token either via flag or config.

**Usage**:

```console
$ fm ngrok BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench.

**Options**:

* `-t, --auth-token`: Ngrok authentication token
* `--save-token/--no-save-token`: Save or don't save the ngrok auth token to config for future use


## Examples

### Create ngrok tunnel for bench

Creates a public ngrok tunnel for the bench using a specified auth token.

```bash
fm ngrok mybench --auth-token YOUR_TOKEN
```

### Use saved auth token from config

Uses an auth token stored in FM configuration to create the tunnel without passing it on the command line.

```bash
fm ngrok mybench
```

