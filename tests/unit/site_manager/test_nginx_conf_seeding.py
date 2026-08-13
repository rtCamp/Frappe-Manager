"""The bench nginx config folder must be seeded from the image, whoever got there first.

`configs/nginx/conf` is bind-mounted over `/etc/nginx`, so it REPLACES what the image
ships. If fm does not copy the image's base config onto the host, nginx starts with no
`nginx.conf` and exits immediately, and the site serves 503.

The seeding used to be guarded on the directory not existing. That held only while nothing
else wrote into `conf/` first. `ensure_fm_nginx_confs` writes `conf/custom/real-ip.conf`
from `generate_compose`, which runs BEFORE `create_compose_dirs`, so the directory was
already there, the copy was skipped, and every newly created bench came up with a dead web
server. Guarding on the marker file instead makes the step independent of ordering and lets
an already-broken bench repair itself.
"""

from pathlib import Path
from unittest.mock import MagicMock

from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps

IMAGE_FILES = {
    "nginx.conf": "events {}\n",
    "mime.types": "types {}\n",
    "conf.d/default.conf": "server { listen 80; }\n",
    "custom/upload-limit.conf": "client_max_body_size 50m;\n",
}


def _ops() -> BenchDockerOps:
    ops = object.__new__(BenchDockerOps)
    ops.docker_client = MagicMock()
    ops.output = MagicMock()
    return ops


def _fake_image_copy():
    """Stand in for `host_run_cp`: materialise the image's /etc/nginx at the destination.

    Mirrors `docker cp` semantics for a destination that does NOT exist, which is the only
    shape the helper ever asks for now that it stages into a scratch directory.
    """

    def _copy(image, source, destination, docker):  # noqa: ARG001
        dest = Path(destination)
        assert not dest.exists(), "docker cp nests instead of merging when the target exists"
        for rel, body in IMAGE_FILES.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        # the real image ships `modules` as a symlink to a path OUTSIDE /etc/nginx, so it
        # dangles inside any copy of that directory
        (dest / "modules").symlink_to("/usr/lib/nginx/modules")

    return _copy


def test_seeding_merges_into_a_folder_fm_already_wrote_into(tmp_path, monkeypatch):
    """The regression: `custom/real-ip.conf` exists first, and the base config must still land."""
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_docker.host_run_cp", _fake_image_copy()
    )
    conf = tmp_path / "configs" / "nginx" / "conf"
    (conf / "custom").mkdir(parents=True)
    (conf / "custom" / "real-ip.conf").write_text("set_real_ip_from 10.0.0.0/8;\n")

    _ops()._seed_nginx_conf(conf, "nginx:test")

    # the base config nginx actually needs
    assert (conf / "nginx.conf").read_text() == "events {}\n"
    assert (conf / "conf.d" / "default.conf").exists()
    # nested, not merged, would look like this and nginx would never read it
    assert not (conf / "nginx").exists()
    # fm's overlay survived
    assert (conf / "custom" / "real-ip.conf").read_text() == "set_real_ip_from 10.0.0.0/8;\n"
    # and the image's own file in the same directory came across too
    assert (conf / "custom" / "upload-limit.conf").exists()


def test_seeding_never_overwrites_what_is_already_on_the_host(tmp_path, monkeypatch):
    """A bench that has been tuned by hand keeps its edits; seeding only fills gaps."""
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_docker.host_run_cp", _fake_image_copy()
    )
    conf = tmp_path / "configs" / "nginx" / "conf"
    conf.mkdir(parents=True)
    (conf / "nginx.conf").write_text("# hand-tuned\n")

    _ops()._seed_nginx_conf(conf, "nginx:test")

    assert (conf / "nginx.conf").read_text() == "# hand-tuned\n"
    assert (conf / "mime.types").exists()


def test_seeding_leaves_no_scratch_directory_behind(tmp_path, monkeypatch):
    """The staging directory is an implementation detail and must not survive."""
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_docker.host_run_cp", _fake_image_copy()
    )
    conf = tmp_path / "configs" / "nginx" / "conf"

    _ops()._seed_nginx_conf(conf, "nginx:test")

    assert (conf / "nginx.conf").exists()
    assert sorted(p.name for p in conf.parent.iterdir()) == ["conf"]


def test_seeding_recreates_the_dangling_modules_symlink(tmp_path, monkeypatch):
    """`/etc/nginx/modules` points outside the directory, so it dangles in any copy of it.

    Following it raises FileNotFoundError and aborts the whole seeding, which is exactly how
    this failed on a real server: the create died on `.../conf/modules`. The link must be
    recreated as a link, not resolved.
    """
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_docker.host_run_cp", _fake_image_copy()
    )
    conf = tmp_path / "configs" / "nginx" / "conf"

    _ops()._seed_nginx_conf(conf, "nginx:test")

    link = conf / "modules"
    assert link.is_symlink()
    assert str(link.readlink()) == "/usr/lib/nginx/modules"
    # and the seeding did not abort partway through on it
    assert (conf / "nginx.conf").exists()
