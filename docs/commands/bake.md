## `fm bake`

Bake an immutable app image.

Two modes:

- With a bench name: bakes that bench's apps.
- With --apps or --config and no bench name: builds with no bench, compose project or site.

**Usage**:

```console
$ fm bake BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to bake. Omit for a standalone bake driven by --apps/--config.

**Options**:

* `--image`: Image to build. A full ref (ghcr.io/acme/mysite:v42) is built as-is; a bare repo (ghcr.io/acme/mysite) gets a generated :<timestamp>-<sha> tag. Defaults to the bench's configured image.
* `--base-image`: Image the runtime Dockerfile builds FROM. Defaults to \[build].base_image, else fm's published frappe image for this fm version.
* `--push/--no-push`: Push the baked image to the registry after building. Defaults to \[build].push, which is off unless set. A bake that does not push still loads the image into the local daemon.
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

### Bake an exact image reference

A ref that already carries a tag is built verbatim; drop the tag to get a generated :<timestamp>-<sha> instead.

```bash
fm bake mybench --image ghcr.io/acme/mysite:v42 --push
```

### Pin the base image the build starts FROM

--base-image is what the runtime Dockerfile builds FROM, while --image is what the bake produces.

```bash
fm bake mybench --base-image ghcr.io/acme/frappe-custom:v15
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
