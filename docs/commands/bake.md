# `fm bake`

Bake an immutable app image.

Baking only builds. The image is always loaded into the local daemon and pushed when asked, but an image-runtime bench keeps serving its current tag until fm switch deploys the new one. A mount bench cannot switch to a bake at all, since runtime is fixed at create time, so its image is for seeding a new bench with fm create --runtime image. Each bake also builds the matching <repo>-nginx:<tag> assets image, and a push sends both.

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

* `--app-image TEXT`: App image to build. A reference carrying a version (ghcr.io/acme/mysite:v42) is built as-is; a bare repository (ghcr.io/acme/mysite) gets a generated :<timestamp>-<sha> tag. Defaults to the bench's configured image.
* `--nginx-image TEXT`: Companion assets image to build beside the app image. Defaults to <app repository>-nginx carrying the app image's tag. Whichever is used is recorded onto the app image, so nothing downstream has to work it out from the name.
* `--base-image TEXT`: Image the runtime Dockerfile builds FROM. Defaults to \[build].base_image, else fm's published frappe image for this fm version.
* `--push/--no-push`: Push the baked image to the registry after building. Defaults to \[build].push, which is off unless set. A bake that does not push still loads the image into the local daemon.
* `--config TEXT`: TOML overlay, either a file path or inline TOML. With a bench it is merged into bench_config.toml and stays there; standalone it supplies the whole config. Repeatable; later --config wins.
* `-a, --apps TEXT`: Standalone bake only: apps to bake (appname:branch or appname, e.g. erpnext:version-16). Repeatable.
* `--python TEXT`: Standalone bake only: Python version to bake.
* `--node TEXT`: Standalone bake only: Node version to bake.
* `-t, --github-token TEXT`: Standalone bake only: GitHub token for private app repos (or use GITHUB_TOKEN env var).
* `--source TEXT`: Where app code comes from: 'provision' (default) clones and installs fresh, 'workspace' snapshots the bench's current on-disk workspace (bench mode only).
* `--include TEXT`: Host path to copy into the image, as 'src' or 'src:dest' with dest relative to the bench root (default: the src basename). Overwrites whatever the app source put there. Repeatable.

## Examples

### Bake an image from an existing bench

```bash
fm bake mybench
```

### Bake into a specific image repository

```bash
fm bake mybench --app-image local/mybench
```

### Bake an exact image reference

A reference that already carries a version is built verbatim; drop it to get a generated :<timestamp>-<sha> instead.

```bash
fm bake mybench --app-image ghcr.io/acme/mysite:v42 --push
```

### Name the companion assets image too

Omitted, the companion is <app repository>-nginx carrying the app image's tag. Either way the pair is recorded onto the app image.

```bash
fm bake mybench --app-image ghcr.io/acme/mysite:v42 --nginx-image ghcr.io/acme/mysite-assets:v42
```

### Pin the base image the build starts FROM

--base-image is what the runtime Dockerfile builds FROM, while --app-image is what the bake produces.

```bash
fm bake mybench --base-image ghcr.io/acme/frappe-custom:v15
```

### Bake exactly what is on disk right now

```bash
fm bake mybench --source workspace
```

### Standalone bake, no bench involved

```bash
fm bake --apps erpnext:version-16 --app-image ghcr.io/acme/mysite --push
```

### Standalone bake from a config file

The config supplies the image, [[apps]] and [build]; nothing else on disk is needed.

```bash
fm bake --config ci/build.toml
```

## See also

- [Deployment](../deploy/index.md)
