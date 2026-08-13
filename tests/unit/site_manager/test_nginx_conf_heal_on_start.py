"""A bench whose nginx base config is missing must repair itself on the next start.

`create_compose_dirs` (which seeds `configs/nginx/conf/` from the nginx image) runs only at
bench CREATION. A bench created while the seeding was guarded on the `conf/` directory
existing -- rather than on the `nginx.conf` marker file -- ended up with fm's two overlay
files and none of the image's base config: nginx died with `/etc/nginx/nginx.conf: No such
file or directory` and the site served HTTP 503, and no command a user ran ever re-seeded it.

`Bench.start` therefore runs the seeding on the way in, before any container comes up. These
tests defend the three properties that makes safe: it heals a broken bench, it is invisible on
a healthy one (a hand-tuned conf is never touched and the seeding is not even entered), and a
heal that fails is reported but never blocks the start.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps
from frappe_manager.site_manager.site import Bench

pytestmark = pytest.mark.timeout(15)

IMAGE_FILES = {
    "nginx.conf": "events {}\n",
    "mime.types": "types {}\n",
    "conf.d/default.conf": "server { listen 80; }\n",
}


def _fake_image_copy(calls: list[dict]):
    """Stand in for `host_run_cp`: materialise the image's /etc/nginx at the destination."""

    def _copy(image, source, destination, docker):
        calls.append({"image": image, "source": source, "destination": destination})
        dest = Path(destination)
        for rel, body in IMAGE_FILES.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        (dest / "modules").symlink_to("/usr/lib/nginx/modules")

    return _copy


def _real_ops(path: Path) -> BenchDockerOps:
    """Real docker ops (so the production seeding runs) with only docker itself mocked."""
    ops = object.__new__(BenchDockerOps)
    ops.docker_client = MagicMock()
    ops.output = MagicMock()
    ops.path = path
    ops.config = SimpleNamespace(runtime=BenchRuntime.mount)
    ops.compose_file_manager = MagicMock()
    ops.compose_file_manager.yml = {"services": {"nginx": {"image": "nginx:test"}, "frappe": {"image": "frappe:test"}}}
    return ops


def _bench(path: Path, docker_ops) -> Bench:
    bench = Bench.__new__(Bench)  # bypass __init__: no docker, no compose, no services
    bench.name = "heal.localhost"
    bench.path = path
    bench.logger = MagicMock()
    bench.output = MagicMock()
    bench.docker_ops = docker_ops
    bench.orchestrator = MagicMock()
    return bench


def _conf_dir(path: Path) -> Path:
    return path / "configs" / "nginx" / "conf"


def _break_bench(path: Path) -> Path:
    """The 739e629 damage: fm's overlay is on disk, the image's base config is not."""
    conf = _conf_dir(path)
    (conf / "custom").mkdir(parents=True)
    (conf / "custom" / "real-ip.conf").write_text("set_real_ip_from 10.0.0.0/8;\n")
    return conf


def test_a_bench_missing_nginx_conf_is_healed_on_start(tmp_path, monkeypatch):
    """The base config lands and fm's overlay survives, and the start still happens."""
    monkeypatch.setattr("frappe_manager.site_manager.modules.bench_docker.host_run_cp", _fake_image_copy([]))
    conf = _break_bench(tmp_path)
    bench = _bench(tmp_path, _real_ops(tmp_path))

    bench.start()

    assert (conf / "nginx.conf").read_text() == "events {}\n"
    assert (conf / "conf.d" / "default.conf").exists()
    assert (conf / "custom" / "real-ip.conf").read_text() == "set_real_ip_from 10.0.0.0/8;\n"
    assert bench.orchestrator.start_bench.call_count == 1


def test_the_heal_lands_before_any_container_is_started(tmp_path, monkeypatch):
    """Ordering is the whole point: nginx must find its config when it boots, not after."""
    monkeypatch.setattr("frappe_manager.site_manager.modules.bench_docker.host_run_cp", _fake_image_copy([]))
    conf = _break_bench(tmp_path)
    bench = _bench(tmp_path, _real_ops(tmp_path))
    seen: list[bool] = []
    bench.orchestrator.start_bench.side_effect = lambda **_: seen.append((conf / "nginx.conf").exists())

    bench.start()

    assert seen == [True]


def test_a_hand_tuned_nginx_conf_is_never_touched(tmp_path, monkeypatch):
    """An operator's edited base config is not clobbered on every start."""
    tuned = "worker_processes 8;\nevents { worker_connections 4096; }\n"
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_docker.host_run_cp",
        lambda **_: pytest.fail("a healthy bench must not be re-seeded from the image"),
    )
    conf = _conf_dir(tmp_path)
    conf.mkdir(parents=True)
    (conf / "nginx.conf").write_text(tuned)
    bench = _bench(tmp_path, _real_ops(tmp_path))

    bench.start()

    assert (conf / "nginx.conf").read_text() == tuned


def test_a_healthy_bench_does_not_enter_the_seeding_at_all(tmp_path):
    """No directory churn and no compose/image lookups on the common path."""
    conf = _conf_dir(tmp_path)
    conf.mkdir(parents=True)
    (conf / "nginx.conf").write_text("events {}\n")
    docker_ops = MagicMock()
    bench = _bench(tmp_path, docker_ops)

    bench.start()

    docker_ops.create_compose_dirs.assert_not_called()
    assert bench.orchestrator.start_bench.call_count == 1


def test_healing_does_not_recopy_the_prebaked_runtimes(tmp_path, monkeypatch):
    """Only nginx is copied out of an image: `.uv`/`.fnm` are hundreds of MB and already placed
    at creation (and deliberately absent from the host in image runtime)."""
    calls: list[dict] = []
    monkeypatch.setattr("frappe_manager.site_manager.modules.bench_docker.host_run_cp", _fake_image_copy(calls))
    _break_bench(tmp_path)
    bench = _bench(tmp_path, _real_ops(tmp_path))

    bench.start()

    assert [call["source"] for call in calls] == ["/etc/nginx"]
    assert not (tmp_path / "workspace" / "frappe-bench" / ".uv").exists()
    assert not (tmp_path / "workspace" / "frappe-bench" / ".fnm").exists()


def test_the_heal_asks_for_the_cheap_variant(tmp_path):
    """The runtime skip is a decision of the start path, not of the seeding it delegates to."""
    docker_ops = MagicMock()
    bench = _bench(tmp_path, docker_ops)

    bench.start()

    docker_ops.create_compose_dirs.assert_called_once_with(copy_runtimes=False)


def test_a_failed_heal_does_not_block_the_start_and_is_reported(tmp_path):
    """Healing is best effort, but never silent."""
    docker_ops = MagicMock()
    docker_ops.create_compose_dirs.side_effect = RuntimeError("nginx:test not present locally")
    bench = _bench(tmp_path, docker_ops)

    bench.start()

    assert bench.orchestrator.start_bench.call_count == 1
    warnings = " ".join(str(call.args[0]) for call in bench.output.warning.call_args_list)
    assert "nginx:test not present locally" in warnings


def test_a_bench_with_no_config_directory_at_all_still_starts(tmp_path, monkeypatch):
    """Nothing on disk and docker unreachable: the worst case must degrade, not raise."""
    monkeypatch.setattr(
        "frappe_manager.site_manager.modules.bench_docker.host_run_cp",
        MagicMock(side_effect=RuntimeError("cannot connect to the docker daemon")),
    )
    bench = _bench(tmp_path, _real_ops(tmp_path))

    bench.start()

    assert bench.orchestrator.start_bench.call_count == 1
    warnings = " ".join(str(call.args[0]) for call in bench.output.warning.call_args_list)
    assert "cannot connect to the docker daemon" in warnings
