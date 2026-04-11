# fm stop

Stop a bench. This cleanly stops all containers and services belonging to the bench.

Usage:

```console
$ fm stop BENCHNAME
```

Example:

```bash
fm stop mybench
```

To stop global services use `fm services stop all`.

!!! tip
    If a bench does not stop cleanly, try `fm restart mybench --container` to force a container restart.
