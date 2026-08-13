"""A workers re-pin (deploy/switch/rollback) is image-only and idempotent.

`pin_workers_to_image` delegates to the compose_shape projection: the first pin
converts the mount-template shape (base image + wholesale ./workspace bind) to
the app image + data-only binds; later re-pins change ONLY the image tag --
no duplicated data binds, and any user-added mount is preserved. Reads dedupe
via a set, so assertions inspect the raw compose yaml where duplicates would
actually be written.
"""

from types import SimpleNamespace

from frappe_manager.docker import DockerVolumeMount, DockerVolumeType
from frappe_manager.docker.compose_file import ComposeFile
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.modules.deploy_orchestrator import pin_workers_to_image

COMPOSE = """\
services:
  long-worker:
    image: base:1
    volumes:
      - fm-sockets:/fm-sockets
      - ./workspace:/workspace
volumes:
  fm-sockets:
"""


def _workers(tmp_path, site="s.localhost"):
    p = tmp_path / "docker-compose.workers.yml"
    p.write_text(COMPOSE)
    cfm = ComposeFile(p)
    cfg = SimpleNamespace(
        runtime=BenchRuntime.image, name=site, base_image=None, deploy_state=None, database=None, redis=None
    )
    return SimpleNamespace(compose_path=p, compose_file_manager=cfm, bench=SimpleNamespace(bench_config=cfg))


def _raw(workers):
    return list(workers.compose_file_manager.yml["services"]["long-worker"]["volumes"])


def test_first_pin_converts_to_image_shape(tmp_path):
    w = _workers(tmp_path)
    pin_workers_to_image(w, "s.localhost", "repo:t1")

    assert w.compose_file_manager.yml["services"]["long-worker"]["image"] == "repo:t1"
    raw = _raw(w)
    assert "./workspace:/workspace" not in raw  # wholesale bind dropped
    assert "fm-sockets:/fm-sockets" in raw  # named volume kept
    assert "./workspace/frappe-bench/logs:/workspace/frappe-bench/logs" in raw
    assert len(raw) == len(set(raw))


def test_repin_is_image_only_and_preserves_user_mount(tmp_path):
    w = _workers(tmp_path)
    pin_workers_to_image(w, "s.localhost", "repo:t1")

    # user adds a custom mount for their convenience
    cfm = w.compose_file_manager
    vols = cfm.get_service_volumes("long-worker")
    vols.append(
        DockerVolumeMount(host="./mydata", container="/mydata", type=DockerVolumeType.bind, compose_path=w.compose_path)
    )
    cfm.set_service_volumes("long-worker", vols)
    before = sorted(_raw(w))

    pin_workers_to_image(w, "s.localhost", "repo:t2")  # deploy re-pin

    assert cfm.yml["services"]["long-worker"]["image"] == "repo:t2"  # image changed
    after = _raw(w)
    assert sorted(after) == before  # volumes untouched
    assert any(v.endswith(":/mydata") for v in after)  # user mount preserved
    assert len(after) == len(set(after))  # no duplicated data binds


def test_pin_noop_without_workers_compose(tmp_path):
    w = SimpleNamespace(compose_path=tmp_path / "missing.yml")
    pin_workers_to_image(w, "s.localhost", "repo:t1")  # must not raise
