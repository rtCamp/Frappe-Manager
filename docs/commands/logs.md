## `fm logs`

Show a bench's web server log, or a container's log with --service.

Without --service this reads the bench's log files on the host, so it works whether or not the bench is up. With --service the logs come from docker and that container has to be running.

**Usage**:

```console
$ fm logs BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

**Options**:

* `--service`: Compose service whose container logs to show (frappe, nginx, redis-cache, ...).
* `-f, --follow`: Keep streaming new lines until Ctrl+C.


## Examples

### Read the bench's web server log

```bash
fm logs mybench
```

### Follow it live

```bash
fm logs mybench -f
```

### Read a container's logs instead

```bash
fm logs mybench --service nginx -f
```

## Related

- [Logs & Debugging](../reference/logs.md)
