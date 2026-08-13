"""Regression guard for nginx-image asset materialization.

`sites/assets/<app>` is a symlink to an absolute *container* path
(`/workspace/frappe-bench/apps/<app>/.../public`). The nginx image has no
`apps/`, so a naive copy leaves a dangling symlink and assets 404. The bake must
remap that container path to the provisioned host tree and copy real files.
"""

from pathlib import Path

from frappe_manager.site_manager.modules.bake import BakeManager


def _bench_tree(root: Path) -> Path:
    """Build a fake provisioned frappe-bench with a container-absolute asset symlink."""
    fb = root / "workspace" / "frappe-bench"
    public = fb / "apps" / "frappe" / "frappe" / "public"
    (public / "dist" / "css").mkdir(parents=True)
    (public / "dist" / "css" / "desk.bundle.css").write_text("body{}")

    assets = fb / "sites" / "assets"
    assets.mkdir(parents=True)
    (assets / "assets.json").write_text("{}")
    (assets / "css").mkdir()
    (assets / "css" / "keep.css").write_text("/* real */")
    # container-absolute symlink (meaningless on the host filesystem)
    (assets / "frappe").symlink_to("/workspace/frappe-bench/apps/frappe/frappe/public")
    return fb


def test_materialize_resolves_container_symlink(tmp_path):
    fb = _bench_tree(tmp_path)
    dest = tmp_path / "ctx" / "sites" / "assets"

    bm = object.__new__(BakeManager)  # bypass __init__ (no Docker)
    bm._materialize_assets(fb / "sites" / "assets", fb, dest)

    built = dest / "frappe" / "dist" / "css" / "desk.bundle.css"
    assert built.is_file()
    assert not built.is_symlink()
    assert built.read_text() == "body{}"
    # the app entry is materialized as a real dir, not a dangling symlink
    assert (dest / "frappe").is_dir()
    assert not (dest / "frappe").is_symlink()
    # non-symlink entries are copied verbatim
    assert (dest / "assets.json").read_text() == "{}"
    assert (dest / "css" / "keep.css").is_file()
