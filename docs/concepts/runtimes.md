# Runtimes: Mount vs Image

The runtime is the most consequential property of a bench: it decides **where the code lives** and therefore what you can do with the bench.

| | `mount` (default) | `image` |
|---|---|---|
| Code lives in | an editable **workspace** on your disk, bind-mounted into the containers | an immutable **Docker image**, baked ahead of time |
| Change code by | editing files (changes are live) | building a new image and switching to it (`fm deploy` does both) |
| Made for | development, simple servers | production: repeatable deploys, instant rollbacks |

## Mount: the editable workspace

```bash
fm create mybench                          # clone + install apps into a workspace
fm create mybench --from-image repo:tag    # or seed the workspace from a baked image (near-instant)
```

Your apps live at `~/frappe/sites/<bench>/workspace/frappe-bench/apps/`, a normal bench directory you can edit, commit from, and debug against. Everything code-related works here:

- `fm update --apps app:branch`: graft apps onto the bench (`appname:ref` or `org/repo:ref`; replaced code is stashed, never deleted, then assets rebuild and the site migrates)
- `fm update --python 3.12 --node 22`: swap toolchains (recreates the venv and reinstalls apps; `--no-recreate-python-env` keeps the existing venv)
- `fm bake`: provision this bench's apps into an immutable image, or snapshot the workspace as it stands with `--source workspace`

## Image: the immutable release

```bash
fm bake mybench                                  # build the image pair from a bench (prints both tags)
fm create prodbench --runtime image --image repo:tag   # or create a bench directly on a pre-built image
```

A bake produces two images: the app image holds the code, the venv and the built assets, and the paired `<repo>-nginx` image holds those assets again for the bench's nginx to serve. The bench itself keeps only mutable data host-side: the site directory, `common_site_config.json`, `apps.txt`, logs and config. The database is never in an image; it stays on whichever server the bench uses, `global-db` or an external one. There is nothing to edit, and that's the point:

- deploys are atomic and repeatable, and rollback is one command away; see [Deployment](../deploy/index.md) and [Rolling back](../deploy/rollback.md)
- `fm update` accepts settings only: environment, alias domains, admin tools, upload limit, restart policy, NewRelic, external-database CA. `--apps`, `--python`, `--node` and `--developer-mode enable` are refused, since those are baked in

The full pipeline (baking, zero-downtime rolling swaps, rollbacks with DB restore, release pruning) is covered in the [Deployment guide](../deploy/index.md).

## Moving between runtimes

```mermaid
stateDiagram-v2
    direction LR
    [*] --> mount : fm create
    [*] --> image : fm create --runtime image --image TAG
    mount --> image : config edit + fm switch BENCH TAG
    image --> mount : fm update --runtime mount
    mount --> mount : fm bake
    image --> image : fm deploy / fm switch TAG / --previous
```

Both directions preserve your site and database:

- **mount → image**: a one-time config edit (`runtime = "image"` + a top-level `image` repo in `bench_config.toml`), then `fm switch <bench> <tag>` runs the full deploy pipeline: the site is migrated onto the image and the workspace stops being the source of truth. The [Deployment guide](../deploy/index.md) walks through it.
- **image → mount** (demotion): `fm update <bench> --runtime mount` extracts an editable workspace from the *currently deployed* image; code on disk equals running code, so no migrate is needed; any stale workspace leftovers are stashed, never deleted.

The backing keys ([`runtime`](../reference/configuration.md#runtime), [`image` and friends](../reference/configuration.md#images)) are documented in the configuration reference.

## How runtime and environment combine

Runtime says where code lives; [environment](../guides/environments.md) says how the web process runs. All four combinations are valid; see the [Concepts overview](index.md) for the 2x2 matrix.

The one asymmetry: developer mode is refused on an image bench even in a `dev` environment, because DocType authoring writes app source files into the container layer that the next deploy throws away.

One flag worth knowing changes meaning with the runtime: `fm create --image` names the *base* frappe image the workspace runs on for a mount bench, and the *app image to run* for an image bench.

## Where to next

- Daily work on a mount bench → [Guides](../guides/index.md)
- Shipping an image bench → [Deployment](../deploy/index.md)
