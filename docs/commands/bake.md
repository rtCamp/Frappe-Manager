## `fm bake`

Bake an immutable app image.

Two modes:

- Bench: `fm bake <bench>` provisions the named bench's apps into a temp
  build context and builds a runtime image.
- Standalone: `fm bake --apps ... --image ...` (or `--config`) builds an
  image with no bench/compose/site -- for CI "build once -> push -> deploy".

Both provision via docker run and COPY the provisioned frappe-bench onto the base image (keeping the supervisor entrypoint).

**Usage**:

```console
$ fm bake BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Bench to bake. Omit for a standalone bake driven by --apps/--config.

**Options**:

* `--image`: Image repository to bake into (sets the top-level image).
* `--tag`: Full image tag to build (overrides the auto-generated <repo>:<timestamp>-<sha>).
* `--push/--no-push`: Push the baked image to the registry (default: push when [registry] is configured for 'registry').
* `--config`: TOML config overlay: a file path or inline TOML content, deep-merged into the config before baking. Repeatable; later --config wins.
* `-a, --apps`: Standalone bake only: apps to bake (appname:branch or appname, e.g. erpnext:version-15). Repeatable.
* `--python`: Standalone bake only: Python version to bake (sets [build].python_version).
* `--node`: Standalone bake only: Node version to bake (sets [build].node_version).
* `-t, --github-token`: Standalone bake only: GitHub token for private app repos (or use GITHUB_TOKEN env var).
* `--source`: App source: 'provision' (default, clone+install fresh) or 'workspace' (snapshot the bench's current on-disk workspace; bench mode only).
* `--include`: Extra path to bake into the image: 'src' or 'src:dest' (dest relative to the bench root, i.e. /workspace/frappe-bench). Applied after source; overrides. Repeatable.


## Examples

### Bake an image from an existing bench

Provisions the bench's apps into a build context and builds a runtime image tagged from the top-level image.

```bash
fm bake mybench
```

### Bake with an explicit image repository

Sets the top-level image for this bake. The tag is derived automatically as <repo>:<timestamp>-<git sha>.

```bash
fm bake mybench --image local/mybench
```

### Standalone bake (no bench) from apps

Builds an image directly from apps -- no bench/compose/site. Ideal for CI 'build once -> push -> deploy elsewhere'.

```bash
fm bake --apps erpnext:version-15 --image ghcr.io/acme/mysite --push
```

### Standalone bake from a config file

The config supplies top-level image, [[apps]] and [build]; nothing else on disk is needed.

```bash
fm bake --config ci/build.toml
```

