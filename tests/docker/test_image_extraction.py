"""What a seeded workspace looks like on disk after a real extraction.

`fm create --apps-from IMAGE` pulls a whole provisioned bench out of an image. The unit
suite replaces the extraction with a recorder, so the thing it exists to produce -- files
at the paths the mount bind expects, owned by the invoking user -- has never been checked.
"""

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from frappe_manager.site_manager.modules.workspace_seed import SEED_PATHS, materialize_workspace_from_image
from frappe_manager.utils.docker import fix_host_path_ownership

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600)]

# One file per seeded path, so "the directory exists" cannot pass for "the contents arrived".
EXPECTED_FILES = {
    "apps": Path("apps/frappe/.git/HEAD"),
    "env": Path("env/bin/python"),
    ".uv": Path(".uv/python-default"),
    ".fnm": Path(".fnm/aliases/default"),
    "sites/assets": Path("sites/assets/css/app.css"),
}


def _daemon_is_native_linux() -> bool:
    if platform.system() != "Linux":
        return False
    out = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Os}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip() == "linux"


def test_seeding_produces_every_path_the_mount_bind_expects(docker_client, fixture_image, tmp_path: Path):
    """Every seeded path arrives with its contents, not just its directory."""
    bench_dir = tmp_path / "workspace" / "frappe-bench"

    extracted = materialize_workspace_from_image(docker_client, fixture_image, bench_dir)

    assert extracted == list(SEED_PATHS)
    for rel in SEED_PATHS:
        probe = bench_dir / EXPECTED_FILES[rel]
        assert probe.is_file(), f"{rel} seeded without {probe.relative_to(bench_dir)}"


def test_empty_skeleton_directory_is_replaced_not_nested(docker_client, fixture_image, tmp_path: Path):
    """A mount create pre-makes empty `apps/`; the extraction must fill it, not create `apps/apps`."""
    bench_dir = tmp_path / "workspace" / "frappe-bench"
    (bench_dir / "apps").mkdir(parents=True)

    materialize_workspace_from_image(docker_client, fixture_image, bench_dir)

    assert (bench_dir / "apps" / "frappe" / "setup.py").is_file()
    assert not (bench_dir / "apps" / "apps").exists()


def test_a_real_workspace_is_never_clobbered(docker_client, fixture_image, tmp_path: Path):
    """A non-empty destination is somebody's work; seeding skips it and says it skipped it."""
    bench_dir = tmp_path / "workspace" / "frappe-bench"
    mine = bench_dir / "apps" / "mine" / "marker.txt"
    mine.parent.mkdir(parents=True)
    mine.write_text("do not delete\n")

    extracted = materialize_workspace_from_image(docker_client, fixture_image, bench_dir)

    assert "apps" not in extracted
    assert mine.read_text() == "do not delete\n"
    assert not (bench_dir / "apps" / "frappe").exists()


@pytest.mark.skipif(not _daemon_is_native_linux(), reason="root-owned bind mounts need a native Linux daemon")
def test_root_owned_paths_are_handed_back_to_the_user(fixture_image, tmp_path: Path):
    """Docker creates a missing bind source as root; fm must give it back or the bench cannot write."""
    victim = tmp_path / "logs"
    victim.mkdir()
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker", "run", "--rm", "--user", "root", "--entrypoint", "",
            "-v", f"{victim}:{victim}", fixture_image,
            "bash", "-c", f"touch {victim}/root.log && chown -R 0:0 {victim}",
        ],
        capture_output=True,
        check=True,
    )
    assert victim.stat().st_uid == 0

    fixed = fix_host_path_ownership(paths=[victim], image=fixture_image)

    assert fixed is True
    assert victim.stat().st_uid == os.getuid()
    assert (victim / "root.log").stat().st_uid == os.getuid()


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker absent")
def test_ownership_fix_is_a_no_op_when_nothing_is_root_owned(fixture_image, tmp_path: Path):
    """Nothing root-owned means no container is started at all; the call is cheap and idempotent."""
    mine = tmp_path / "logs"
    mine.mkdir()

    assert fix_host_path_ownership(paths=[mine], image=fixture_image) is False
