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

from datetime import UTC, datetime
from pathlib import Path

from frappe_manager.utils.docker import fix_host_path_ownership

# Everything code/runtime the image owns; deliberately excludes site data.
SEED_PATHS = ("apps", "env", ".uv", ".fnm", "sites/assets")


class WorkspaceSeedError(Exception):
    """Workspace materialization from an image cannot proceed."""


def stash_conflicting_seed_paths(frappe_bench_dir: Path, output=None) -> Path | None:
    """Move existing non-empty :data:`SEED_PATHS` aside before a demotion re-extract.

    A demotion promises code-on-disk == running image. Leftover trees from an
    earlier mount life (demote -> promote -> demote) are STALE relative to the
    deployed tag and must not be silently kept -- but they may hold uncommitted
    work, so they are renamed (never deleted) into a timestamped stash dir
    inside the workspace. Returns the stash dir when anything moved.
    """
    stash: Path | None = None
    for rel in SEED_PATHS:
        src = frappe_bench_dir / rel
        if not src.exists():
            continue
        if src.is_dir() and not any(src.iterdir()):
            continue  # empty skeletons are handled by materialize itself
        if stash is None:
            stash = frappe_bench_dir / f".fm-demote-stash-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        dest = stash / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)
        if output:
            output.print(f"Stashed stale {rel} -> {dest.relative_to(frappe_bench_dir)}")
    return stash


def _image_arch(docker_client, image: str) -> str | None:
    """Architecture of a local image, or None when it cannot be determined."""
    from frappe_manager.utils.docker import run_command_with_exit_code

    try:
        out = run_command_with_exit_code(
            [*docker_client.docker_cmd, "image", "inspect", "--format", "{{.Architecture}}", image],
            stream=False,
        )
        return " ".join(out.stdout).strip() or None
    except Exception:
        return None


def _daemon_arch(docker_client) -> str | None:
    try:
        return ((docker_client.version() or {}).get("Server") or {}).get("Arch")
    except Exception:
        return None


def materialize_workspace_from_image(docker_client, image: str, frappe_bench_dir: Path, output=None) -> list[str]:
    """Extract :data:`SEED_PATHS` from ``image`` into ``frappe_bench_dir``.

    Existing non-empty destinations are left untouched (idempotent, and never
    clobbers a real workspace); an existing EMPTY directory counts as absent
    (mount create pre-makes skeleton dirs like ``apps/``) and is removed first
    so ``docker cp`` recreates it instead of nesting into it. Returns the list
    of paths actually extracted. Ownership is normalized afterwards
    (``docker cp`` preserves in-image uids).
    """
    daemon_arch = _daemon_arch(docker_client)
    image_arch = _image_arch(docker_client, image)
    if daemon_arch and image_arch and daemon_arch != image_arch:
        raise WorkspaceSeedError(
            f"Image {image} is {image_arch} but the Docker daemon is {daemon_arch}: the extracted "
            "binaries (Python venv, Node) would not run. Use an image baked for this architecture.",
        )

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
    if "apps" in extracted and output:
        app_dirs = [d for d in (frappe_bench_dir / "apps").iterdir() if d.is_dir()]
        if app_dirs and not any((d / ".git").exists() for d in app_dirs):
            output.warning(
                "Extracted apps carry no .git metadata (slim-baked image?): the bench works, "
                "but 'fm bake' from this workspace cannot derive app specs.",
            )
    if extracted:
        fix_host_path_ownership(paths=[frappe_bench_dir / r for r in extracted], image=image, output=output)
    return extracted
