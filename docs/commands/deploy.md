## `fm deploy`

Bake an immutable image from the bench and deploy it.

fm bake and fm switch in one command. The bench must already be in image runtime; a mount-runtime bench is refused.

**Usage**:

```console
$ fm deploy BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench to deploy.  [required]

**Options**:

* `--image`: Image repository to bake into, e.g. local/mybench.
* `--tag`: Full image tag to build, instead of the generated <repo>:<timestamp>-<sha>.
* `--remote`: Host of the remote Docker daemon to deploy to, e.g. deploy.example.com. The SSH user and port come from the deploy config, which also supplies the default host.
* `--push/--no-push`: Push the baked image to the registry. On by default when registry.distribution is 'registry'.
* `--rolling/--no-rolling`: Force or forbid the rolling web swap. The default decides per deploy, rolling only when running old and new containers side by side is safe.
* `--keep`: After a successful deploy, prune old releases keeping the newest N (minimum 1). Same as running fm prune afterwards.
* `--config`: TOML overlay, either a file path or inline TOML, merged into the bench's bench_config.toml before the bake and left there. Repeatable; later --config wins.


## Examples

### Bake and deploy the current bench code

```bash
fm deploy mybench
```

### Deploy to a remote host

The image is baked for the remote daemon's architecture, not the local one.

```bash
fm deploy mybench --remote deploy.example.com
```

### Deploy and trim old releases in one go

```bash
fm deploy mybench --keep 3
```

## Related

- [Deployment guide](../deploy/index.md)
