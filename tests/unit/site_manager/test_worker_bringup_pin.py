"""Pin the shared mechanism every bring-up path (`fm start`, worker autostart, adding a site,
`fm update`'s lighter recreate arm, `fm telemetry enable/disable`) now calls before running
`compose up` against an EXISTING workers compose: the worker image is always re-derived from
`deployments.current.app_image`, never trusted off the file on disk.

Before this fix, a stale file pin (left behind by a failed rolling swap, or by an `fm ssl add`
that pinned without bringing up) survived untouched until the next `compose up` recreated the
workers onto it -- silently, with exit 0 and no warning. These tests write that stale pin to a
REAL compose file and prove a bring-up re-pin overrides it with the recorded deployment, the
same way a deploy's own `pin_workers_to_image` already overrides it with an explicit candidate.
"""

from types import SimpleNamespace

from frappe_manager.docker.compose_file import ComposeFile
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.modules.deploy_orchestrator import (
    pin_workers_to_current_deployment,
    pin_workers_to_image,
)

CURRENT_IMAGE = "registry.example/app:current"
CURRENT_NGINX_IMAGE = "registry.example/app-nginx:current"

#: A stale pin: what a failed rolling swap or a pin-only `fm ssl add` could plausibly leave on
#: disk while `deployments.current` (and the web tier) has already moved on.
WORKERS_COMPOSE = """\
services:
  short-worker:
    image: registry.example/app:stale
    volumes:
      - fm-sockets:/fm-sockets
  long-worker:
    image: registry.example/app:stale
    volumes:
      - fm-sockets:/fm-sockets
volumes:
  fm-sockets:
"""


def _workers(tmp_path, app_image=CURRENT_IMAGE, nginx_image=CURRENT_NGINX_IMAGE):
    """A `BenchWorkers`-shaped double: a REAL workers compose file carrying the stale pin above,
    plus a bench_config recording `app_image` as the bench's CURRENT deployment."""
    path = tmp_path / "docker-compose.workers.yml"
    path.write_text(WORKERS_COMPOSE)
    cfm = ComposeFile(path)
    bench_config = SimpleNamespace(
        runtime=BenchRuntime.image,
        base_image=None,
        deployments=SimpleNamespace(current=SimpleNamespace(app_image=app_image, nginx_image=nginx_image)),
        sites=None,
        site_names=["s.localhost"],
    )
    workers = SimpleNamespace(
        compose_path=path,
        compose_file_manager=cfm,
        bench=SimpleNamespace(bench_config=bench_config),
    )
    return workers, cfm


class TestPinWorkersToCurrentDeployment:
    def test_a_stale_file_pin_is_overridden_by_the_recorded_deployment(self, tmp_path):
        workers, cfm = _workers(tmp_path)
        pinned = pin_workers_to_current_deployment(workers, ["s.localhost"])
        assert set(pinned) == {"short-worker", "long-worker"}
        assert cfm.yml["services"]["short-worker"]["image"] == CURRENT_IMAGE
        assert cfm.yml["services"]["long-worker"]["image"] == CURRENT_IMAGE

    def test_the_persisted_file_a_bring_up_compose_up_would_read_is_also_corrected(self, tmp_path):
        """The literal bug: a bring-up `compose up` reads the FILE, not the in-memory object.
        Re-read a fresh `ComposeFile` off disk, exactly as a later process would."""
        workers, _ = _workers(tmp_path)
        pin_workers_to_current_deployment(workers, ["s.localhost"])
        reread = ComposeFile(workers.compose_path)
        assert reread.yml["services"]["short-worker"]["image"] == CURRENT_IMAGE
        assert reread.yml["services"]["long-worker"]["image"] == CURRENT_IMAGE

    def test_no_workers_compose_is_a_noop(self, tmp_path):
        workers, _ = _workers(tmp_path)
        workers.compose_path.unlink()
        assert pin_workers_to_current_deployment(workers, ["s.localhost"]) == []

    def test_no_worker_services_is_a_noop(self, tmp_path):
        path = tmp_path / "docker-compose.workers.yml"
        path.write_text("services: {}\n")
        workers = SimpleNamespace(
            compose_path=path,
            compose_file_manager=ComposeFile(path),
            bench=SimpleNamespace(
                bench_config=SimpleNamespace(
                    runtime=BenchRuntime.image,
                    base_image=None,
                    deployments=SimpleNamespace(
                        current=SimpleNamespace(app_image=CURRENT_IMAGE, nginx_image=CURRENT_NGINX_IMAGE)
                    ),
                    sites=None,
                    site_names=["s.localhost"],
                )
            ),
        )
        assert pin_workers_to_current_deployment(workers, ["s.localhost"]) == []

    def test_an_explicit_candidate_still_wins_over_the_recorded_deployment(self, tmp_path):
        """`pin_workers_to_image` (the deploy/switch/rollback path) must keep pinning its OWN
        candidate rather than falling back to `deployments.current` -- the two functions share
        plumbing, not behaviour."""
        workers, cfm = _workers(tmp_path)
        pin_workers_to_image(
            workers,
            ["s.localhost"],
            "registry.example/app:candidate",
            "registry.example/app-nginx:candidate",
        )
        assert cfm.yml["services"]["short-worker"]["image"] == "registry.example/app:candidate"
        assert cfm.yml["services"]["long-worker"]["image"] == "registry.example/app:candidate"
