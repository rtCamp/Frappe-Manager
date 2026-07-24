"""Materialize an editable (mount) workspace from a baked app image.

The baked image carries the complete provisioned ``/workspace/frappe-bench``
(``apps/`` with .git, ``env/``, ``.uv/``, ``.fnm/``, built ``sites/assets``)
installed against the same container path a mount bench binds its workspace to,
so extracting those paths onto the host yields a working editable tree with no
clone / dependency install / asset build. This is ``fm bake`` in reverse; it
powers the image -> mount runtime demotion (``fm update --runtime mount``) and
image-seeded mount creates.

Site data (``sites/<site>``, ``common_site_config.json``, ``apps.txt``,
``logs/``, ``config/``) is NEVER written: for a demotion it already lives on
the host; for a create it is seeded by the normal create flow.
"""

from pathlib import Path

from frappe_manager.utils.docker import fix_host_path_ownership

# Everything code/runtime the image owns; deliberately excludes site data.
SEED_PATHS = ("apps", "env", ".uv", ".fnm", "sites/assets")


def materialize_workspace_from_image(docker_client, image: str, frappe_bench_dir: Path, output=None) -> list[str]:
    """Extract :data:`SEED_PATHS` from ``image`` into ``frappe_bench_dir``.

    Existing non-empty destinations are left untouched (idempotent, and never
    clobbers a real workspace); an existing EMPTY directory counts as absent
    (mount create pre-makes skeleton dirs like ``apps/``) and is removed first
    so ``docker cp`` recreates it instead of nesting into it. Returns the list
    of paths actually extracted. Ownership is normalized afterwards
    (``docker cp`` preserves in-image uids).
    """
    frappe_bench_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    with docker_client.create_temp_container(image) as container:
        for rel in SEED_PATHS:
            dest = frappe_bench_dir / rel
            if dest.exists():
                if not (dest.is_dir() and not any(dest.iterdir())):
                    continue
                dest.rmdir()  # empty skeleton dir -- cp must create it, not nest into it
            if output:
                output.change_head(f"Extracting {rel} from image")
            dest.parent.mkdir(parents=True, exist_ok=True)
            docker_client.cp(
                source=f"/workspace/frappe-bench/{rel}",
                destination=str(dest),
                source_container=container.name,
                stream=False,
            )
            extracted.append(rel)
    if extracted:
        fix_host_path_ownership(paths=[frappe_bench_dir / r for r in extracted], image=image, output=output)
    return extracted
