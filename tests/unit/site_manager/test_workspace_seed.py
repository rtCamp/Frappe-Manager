"""Workspace seeding contract (image -> editable mount workspace).

Defends: SEED_PATHS extraction, never-clobber semantics, the empty-skeleton-dir
rule (mount create pre-makes dirs like ``apps/``; docker cp must recreate them,
not nest into them), the cross-arch refusal, the .git provenance warning, and
ownership normalization of extracted paths.
"""

from contextlib import contextmanager
from pathlib import Path

import pytest

from frappe_manager.site_manager.modules import workspace_seed
from frappe_manager.site_manager.modules.workspace_seed import (
    SEED_PATHS,
    WorkspaceSeedError,
    materialize_workspace_from_image,
)


class _Container:
    name = "tmp-ctr"


class _StubDocker:
    def __init__(self, arch="arm64"):
        self.docker_cmd = ["docker"]
        self.arch = arch
        self.cps = []

    def version(self):
        return {"Server": {"Arch": self.arch}}

    @contextmanager
    def create_temp_container(self, image):
        yield _Container()

    def cp(self, source, destination, source_container=None, stream=False):
        self.cps.append(source)
        dest = Path(destination)
        dest.mkdir(parents=True)
        (dest / ".seeded").touch()


class _Out:
    def __init__(self):
        self.warnings = []

    def change_head(self, *a, **k):
        pass

    def print(self, *a, **k):
        pass

    def warning(self, msg, *a, **k):
        self.warnings.append(msg)


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(workspace_seed, "_image_arch", lambda *_: "arm64")
    owned = []
    monkeypatch.setattr(workspace_seed, "fix_host_path_ownership", lambda paths, **_kw: owned.extend(paths))
    docker = _StubDocker()
    docker.owned = owned
    return docker


def test_extracts_all_seed_paths_into_empty_tree(tmp_path, stub):
    fb = tmp_path / "frappe-bench"
    extracted = materialize_workspace_from_image(stub, "r:t", fb)
    assert extracted == list(SEED_PATHS)
    assert (fb / "apps" / ".seeded").exists()
    assert stub.owned == [fb / rel for rel in SEED_PATHS]  # ownership normalized


def test_non_empty_destination_is_never_clobbered(tmp_path, stub):
    fb = tmp_path / "frappe-bench"
    (fb / "apps" / "frappe").mkdir(parents=True)
    extracted = materialize_workspace_from_image(stub, "r:t", fb)
    assert "apps" not in extracted
    assert not (fb / "apps" / ".seeded").exists()  # untouched


def test_empty_skeleton_dir_counts_as_absent(tmp_path, stub):
    fb = tmp_path / "frappe-bench"
    (fb / "apps").mkdir(parents=True)
    extracted = materialize_workspace_from_image(stub, "r:t", fb)
    assert "apps" in extracted
    assert (fb / "apps" / ".seeded").exists()
    assert not (fb / "apps" / "apps").exists()  # recreated, not nested


def test_arch_mismatch_refused_before_touching_disk(tmp_path, stub, monkeypatch):
    monkeypatch.setattr(workspace_seed, "_image_arch", lambda *_: "amd64")
    fb = tmp_path / "fb"
    with pytest.raises(WorkspaceSeedError, match="amd64"):
        materialize_workspace_from_image(stub, "r:t", fb)
    assert not fb.exists()


def test_unknown_arch_does_not_block(tmp_path, stub, monkeypatch):
    monkeypatch.setattr(workspace_seed, "_image_arch", lambda *_: None)
    assert materialize_workspace_from_image(stub, "r:t", tmp_path / "fb")


def _cp_with_app_dir(docker, git: bool):
    orig_cp = docker.cp

    def cp(source, destination, source_container=None, stream=False):
        orig_cp(source, destination, source_container=source_container, stream=stream)
        if source.endswith("/apps"):
            app = Path(destination) / "frappe"
            (app / ".git").mkdir(parents=True) if git else app.mkdir(parents=True)

    docker.cp = cp


def test_gitless_apps_warn_on_provenance(tmp_path, stub):
    _cp_with_app_dir(stub, git=False)
    out = _Out()
    materialize_workspace_from_image(stub, "r:t", tmp_path / "fb", output=out)
    assert any(".git" in w for w in out.warnings)


def test_apps_with_git_do_not_warn(tmp_path, stub):
    _cp_with_app_dir(stub, git=True)
    out = _Out()
    materialize_workspace_from_image(stub, "r:t", tmp_path / "fb", output=out)
    assert out.warnings == []


# ------------------------------------------------------------ demotion stash


def test_stash_moves_stale_trees_and_preserves_content(tmp_path):
    fb = tmp_path / "fb"
    (fb / "apps" / "frappe").mkdir(parents=True)
    (fb / "apps" / "frappe" / "work.py").write_text("uncommitted")
    (fb / "sites" / "assets").mkdir(parents=True)
    (fb / "sites" / "assets" / "x.css").touch()
    (fb / "env").mkdir()  # empty skeleton: NOT stashed
    stash = workspace_seed.stash_conflicting_seed_paths(fb)
    assert stash is not None
    assert stash.parent == fb
    assert (stash / "apps" / "frappe" / "work.py").read_text() == "uncommitted"  # renamed, never deleted
    assert (stash / "sites" / "assets" / "x.css").exists()  # nested path structure kept
    assert not (fb / "apps").exists()
    assert (fb / "env").exists()  # empty skeleton untouched
    assert (fb / "sites").exists() and not (fb / "sites" / "assets").exists()


def test_stash_noop_returns_none(tmp_path):
    fb = tmp_path / "fb"
    fb.mkdir()
    (fb / "apps").mkdir()  # empty
    assert workspace_seed.stash_conflicting_seed_paths(fb) is None


def test_stash_then_materialize_extracts_fresh(tmp_path, stub):
    # The demote -> promote -> demote cycle: stale tree stashed, new tag extracted.
    fb = tmp_path / "fb"
    (fb / "apps" / "frappe").mkdir(parents=True)
    (fb / "apps" / "frappe" / "old.py").touch()
    workspace_seed.stash_conflicting_seed_paths(fb)
    extracted = materialize_workspace_from_image(stub, "r:t2", fb)
    assert "apps" in extracted
    assert (fb / "apps" / ".seeded").exists()  # fresh from image
    assert not (fb / "apps" / "frappe" / "old.py").exists()
