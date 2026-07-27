## `fm deploy`

Bake an immutable image from the bench and deploy it (recreate-swap).

Runs the image pipeline: fetch -> pre-flight -> backup -> maintenance ->
drain -> migrate (one-shot new image) -> recreate-swap -> finalize -> record.

Transport (Phase 5): in registry mode the image is pushed after bake and the
(possibly remote) daemon pulls it during fetch; in save_load mode the image
is streamed to the remote via ``docker save | ssh docker load`` before deploy.
With ``--remote`` the local orchestrator drives the remote daemon via
``DOCKER_HOST``.

**Usage**:

```console
$ fm deploy BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench to deploy.  [required]

**Options**:

* `--image`: Image repository to bake into (sets the top-level image).
* `--tag`: Full image tag to build (overrides the auto-generated tag).
* `--remote`: Deploy to a remote daemon over SSH (DOCKER_HOST=ssh://<user>@<host>:<port>). Falls back to [deploy].ssh_server when omitted.
* `--push/--no-push`: Push the baked image to the registry (default: push when [registry] is configured for 'registry').
* `--rolling/--no-rolling`: Force/disable the rolling web swap. Default: auto (rolling whenever the overlap is safe: no migrate, additive-asserted, or migrate under a maintenance window).
* `--keep`: After a successful deploy, prune old releases keeping the newest N (see fm prune).
* `--config`: TOML config overlay: a file path or inline TOML content, deep-merged into the bench config before deploy. Repeatable; later --config wins.


## Examples

### Bake and deploy the current bench code

Bakes a fresh immutable image from the bench and deploys it via recreate-swap.

```bash
fm deploy mybench
```

### Deploy into a specific image repository

Sets the top-level image for this bake+deploy.

```bash
fm deploy mybench --image local/mybench
```

