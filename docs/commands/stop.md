# `fm stop`

Stop a bench's containers, admin tools and workers.

In-flight background jobs are waited for first: a container stop gives a running job ten seconds and then kills it, which leaves the job recorded as still running with nothing to finish it. If they outlast \[workers].drain_timeout the bench is left running and nothing is stopped, so the stop can be retried; --no-drain stops immediately and interrupts them.

Nothing is deleted; fm start brings the bench back.

**Usage**:

```console
$ fm stop BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

**Options**:

* `--drain/--no-drain`: Wait for in-flight RQ jobs before stopping the workers, and abort the stop if they outlast \[workers].drain_timeout; --no-drain interrupts them instead.  [default: true]

## Examples

### Stop a bench

Waits for in-flight RQ jobs first.

```bash
fm stop mybench
```

### Stop now, interrupting running jobs

```bash
fm stop mybench --no-drain
```
