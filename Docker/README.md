# Docker images

`build.sh` builds the two images the bench and its reverse proxy run from:

- `frappe`, built from `frappe/`: the bench container. It runs Gunicorn, the RQ workers, the scheduler and Socketio under supervisord, and has `fmx` installed for driving them.
- `nginx`, built from `nginx/`: the reverse proxy shared in front of the bench.

## Usage

```bash
./build.sh
```

Building needs an editable install of this package (`pip install -e .` from the repo root): the script reads the image tag from the installed `frappe-manager` version via `importlib.metadata`, and fails with a clear error if that lookup comes back empty.

Each image is tagged `ghcr.io/rtcamp/frappe-manager-<image>:v<version>`, for example `ghcr.io/rtcamp/frappe-manager-frappe:v1.2.3`.

By default the script only builds for the host's own architecture and keeps the result local, nothing is pushed. Set `PUSH=true` to push that architecture's image and also create and push a multi-arch manifest at the same `ghcr.io/rtcamp/frappe-manager-<image>:v<version>` tag:

```bash
PUSH=true ./build.sh
```

The manifest step amends both an `amd64` and an `arm64` per-architecture tag together, so a complete multi-arch manifest needs a `PUSH=true` run on each architecture before the manifest push succeeds.
