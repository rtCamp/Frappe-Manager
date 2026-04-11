## `fm stop`

Stop a bench.

Stops all containers for the given bench. No data is removed; containers can be started again with 'fm start'.

**Usage**:

```console
$ fm stop BENCHNAME
```

**Arguments**:

* `BENCHNAME`: Name of the bench.


**Examples**:

_Stop bench containers_
Stops all running containers for the specified bench without removing any data. Use to shut down a bench safely.
```bash
fm stop mybench
```

_Stop multiple benches_
Chain multiple stop commands to shut down several benches at once.
```bash
fm stop mybench && fm stop another-bench
```

