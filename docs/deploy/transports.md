# Transports & Platforms

## Transports: getting the image to where it runs

```mermaid
flowchart LR
    subgraph build [build machine]
        B[fm bake] --> LI[local image]
    end
    subgraph target [target daemon]
        RD[docker daemon] --> RUN[bench containers]
    end
    LI -->|same machine| RD
    LI -->|registry: docker push| REG[(registry)] -->|fetch: docker pull| RD
    LI -->|save_load: docker save over ssh docker load| RD
    B -.->|every pipeline step| DH[DOCKER_HOST=ssh://user@host]
    DH -.-> RD
```

Configured in `bench_config.toml`:

```toml
[registry]
distribution = "registry"    # push after bake; the target pulls during fetch
# distribution = "save_load" # airgap: docker save | ssh <target> docker load

[deploy]
ssh_server = "prod.example.com"   # remote daemon; or pass --remote on fm deploy
ssh_user   = "frappe"
ssh_port   = 22
```

With a remote configured, the **entire pipeline** drives the remote daemon over `DOCKER_HOST=ssh://` - no fm needed on the target. Registry mode encodes the registry host in the top-level `image` (e.g. `ghcr.io/acme/mybench`); when `[registry] registry/username/password` are all set they are used for `docker login`, otherwise ambient docker auth applies.

## Platforms (CPU architectures)

Images are architecture-specific. fm resolves the bake target as:

1. `[build].platform` if set (explicit always wins; a mismatch with the deploy target warns),
2. else, for `fm deploy` with a remote: the **remote daemon's architecture**, auto-detected - the image must match where it *runs*, not where it builds,
3. else the build daemon's native arch.

Cross-arch bakes (e.g. building `linux/amd64` on an Apple Silicon Mac) run the whole bake - provisioning containers and image builds - under `DOCKER_DEFAULT_PLATFORM`, which requires emulation (Rosetta/binfmt; Docker Desktop ships it) and only works with `[build].source = "provision"` (a `workspace` snapshot contains host-arch binaries, so fm refuses it). Before provisioning starts, fm verifies the **base image** actually publishes the target architecture and fails fast with the available list if not. Multi-arch manifest lists are not supported - one platform per bake.

## Running fm in CI

There is no dedicated CI integration or ready-made pipeline recipe yet. But fm has no special host requirements: it runs anywhere Docker and your SSH keys exist, including a CI runner.

The typical shape:

- the runner bakes and pushes the image (registry transport),
- then runs the switch against the production daemon over `DOCKER_HOST=ssh://` (the `[deploy]` remote above).

Nothing needs to be installed on the target server either way.
