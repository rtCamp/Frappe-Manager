## `fm bake`

Bake an immutable app image from a bench.

Provisions the bench's apps into a temporary build context via docker run and
builds a runtime image (COPY of the provisioned frappe-bench onto the base
image, keeping the supervisor entrypoint).

**Usage**:

```console
$ fm bake BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench to bake.  [required]

**Options**:

* `--image`: Image repository to bake into (overrides [deploy].image).
* `--tag`: Full image tag to build (overrides the auto-generated <repo>:<timestamp>-<sha>).
* `--push/--no-push`: Push the baked image to the registry (default: push when [registry] is configured for 'registry').
* `--config`: TOML config overlay: a file path or inline TOML content, deep-merged into the bench config before baking. Repeatable; later --config wins.


## Examples

### Bake an immutable app image for a bench

Provisions the bench's apps into a build context and builds a runtime image tagged from [deploy].image.

```bash
fm bake mybench
```

### Bake with an explicit image repository

Overrides [deploy].image for this bake. The tag is derived automatically as <repo>:<timestamp>-<git sha>.

```bash
fm bake mybench --image local/mybench
```

