## `fm bake`

Bake an immutable app image.

Two modes:

- With a bench name: bakes that bench's apps.
- With --apps or --config and no bench name: builds with no bench, compose project or site.

**Usage**:

```console
$ fm bake BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Bench to bake. Omit for a standalone bake driven by --apps/--config.

**Options**:

* `--image`: Image repository to bake into, e.g. ghcr.io/acme/mysite.
* `--tag`: Full image tag to build, instead of the generated <repo>:<timestamp>-<sha>.
* `--push/--no-push`: Push the baked image to the registry after building. Defaults to [build].push, which is off unless set. A bake that does not push still loads the image into the local daemon.
* `--config`: TOML overlay, either a file path or inline TOML. With a bench it is merged into bench_config.toml and stays there; standalone it supplies the whole config. Repeatable; later --config wins.
* `-a, --apps`: Standalone bake only: apps to bake (appname:branch or appname, e.g. erpnext:version-15). Repeatable.
* `--python`: Standalone bake only: Python version to bake.
* `--node`: Standalone bake only: Node version to bake.
* `-t, --github-token`: Standalone bake only: GitHub token for private app repos (or use GITHUB_TOKEN env var).
* `--source`: Where app code comes from: 'provision' (default) clones and installs fresh, 'workspace' snapshots the bench's current on-disk workspace (bench mode only).
* `--include`: Host path to copy into the image, as 'src' or 'src:dest' with dest relative to the bench root (default: the src basename). Overwrites whatever the app source put there. Repeatable.


## Examples

### Bake an image from an existing bench

```bash
fm bake mybench
```

### Bake into a specific image repository

```bash
fm bake mybench --image local/mybench
```

### Bake exactly what is on disk right now

```bash
fm bake mybench --source workspace
```

### Standalone bake, no bench involved

```bash
fm bake --apps erpnext:version-15 --image ghcr.io/acme/mysite --push
```

### Standalone bake from a config file

The config supplies the image, [[apps]] and [build]; nothing else on disk is needed.

```bash
fm bake --config ci/build.toml
```

## Related

- [Deployment guide](../deploy/index.md)
