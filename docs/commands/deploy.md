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

* `--image`: Image repository to bake into (overrides [deploy].image).
* `--tag`: Full image tag to build (overrides the auto-generated tag).
* `--remote`: Deploy to a remote daemon over SSH (DOCKER_HOST=ssh://<user>@<host>:<port>). Falls back to [remote].ssh_server when omitted.
* `--push/--no-push`: Push the baked image to the registry (default: push when [registry] is configured for 'registry').
* `--rolling/--no-rolling`: Force/disable the rolling (blue-green) web swap. Default: auto (rolling when the deploy is no-migrate or asserts an additive migration).


## Examples

### Bake and deploy the current bench code

Bakes a fresh immutable image from the bench and deploys it via recreate-swap.

```bash
fm deploy mybench
```

### Deploy into a specific image repository

Overrides [deploy].image for this bake+deploy.

```bash
fm deploy mybench --image local/mybench
```

