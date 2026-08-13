"""Seeding recreates symlinks that live *inside* a subdirectory of the image's /etc/nginx.

`_seed_nginx_conf` walks the staged copy in sorted order, so a directory is always created
on the host before the entries under it. The symlink branch then re-creates the link in a
directory that already exists, which is only safe because the mkdir there is idempotent --
and it must not fall back to following/copying the link, because a link pointing outside
/etc/nginx dangles in any copy of the directory and `copy2` would blow up on it.

Companion to test_nginx_conf_seeding.py, which covers the top-level `modules` link only.
"""

from pathlib import Path
from unittest.mock import MagicMock

from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps


def _ops() -> BenchDockerOps:
    ops = object.__new__(BenchDockerOps)
    ops.docker_client = MagicMock()
    ops.output = MagicMock()
    return ops


def _image_with_nested_link():
    """`host_run_cp` stand-in: an image whose conf.d holds a link to an absolute path."""

    def _copy(image, source, destination, docker):
        dest = Path(destination)
        (dest / "conf.d").mkdir(parents=True)
        (dest / "nginx.conf").write_text("events {}\n")
        (dest / "conf.d" / "default.conf").write_text("server { listen 80; }\n")
        # dangles inside the copy, exactly like the real `modules` link
        (dest / "conf.d" / "gzip.conf").symlink_to("/etc/nginx/snippets/gzip.conf")

    return _copy


def _seed(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr("frappe_manager.site_manager.modules.bench_docker.host_run_cp", _image_with_nested_link())
    conf = tmp_path / "configs" / "nginx" / "conf"
    _ops()._seed_nginx_conf(conf, "nginx:test")
    return conf


def test_a_symlink_inside_a_subdirectory_is_recreated_as_a_symlink(tmp_path, monkeypatch):
    conf = _seed(tmp_path, monkeypatch)

    link = conf / "conf.d" / "gzip.conf"
    assert link.is_symlink()
    assert str(link.readlink()) == "/etc/nginx/snippets/gzip.conf"
    # the ordinary file next to it still landed
    assert (conf / "conf.d" / "default.conf").exists()


def test_reseeding_over_the_nested_link_is_a_no_op(tmp_path, monkeypatch):
    conf = _seed(tmp_path, monkeypatch)
    _ops()._seed_nginx_conf(conf, "nginx:test")

    link = conf / "conf.d" / "gzip.conf"
    assert link.is_symlink()
    assert str(link.readlink()) == "/etc/nginx/snippets/gzip.conf"
    assert (conf / "nginx.conf").read_text() == "events {}\n"
