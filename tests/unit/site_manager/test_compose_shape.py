"""Pure-core tests for the compose shape model (strategy/factory/renderer).

The specs are plain data: decisions are asserted without Docker. The renderer
is exercised against a real ComposeFile to prove idempotency and passthrough.
"""

from types import SimpleNamespace

from frappe_manager.docker.compose_file import ComposeFile
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.modules.compose_shape import (
    RenderContext,
    apply_specs,
    bench_service_specs,
    bind_strings,
    runtime_shape,
    worker_service_specs,
)


def _cfg(runtime, name="s.localhost", tag=None, base_image=None):
    return SimpleNamespace(
        runtime=runtime,
        name=name,
        base_image=base_image,
        deploy_state=SimpleNamespace(current_tag=tag) if tag else None,
    )


# ------------------------------------------------------------------ strategy


def test_mount_shape_defaults():
    shape = runtime_shape(_cfg(BenchRuntime.mount))
    assert shape.image("frappe") is None  # template default kept
    assert shape.image("nginx") is None
    assert [(b.host, b.container) for b in shape.binds()] == [("./workspace", "/workspace")]


def test_mount_shape_base_image_override():
    shape = runtime_shape(_cfg(BenchRuntime.mount, base_image="custom/frappe:x1"))
    assert shape.image("frappe") == "custom/frappe:x1"
    assert shape.image("nginx") is None  # nginx keeps stock image in mount


def test_image_shape_tags_and_binds():
    shape = runtime_shape(_cfg(BenchRuntime.image, tag="ghcr.io/acme/erp:jun01"))
    assert shape.image("frappe") == "ghcr.io/acme/erp:jun01"
    assert shape.image("nginx") == "ghcr.io/acme/erp-nginx:jun01"  # paired assets image
    targets = [b.container for b in shape.binds()]
    assert "/workspace" not in targets
    assert "/workspace/frappe-bench/sites/s.localhost" in targets


def test_image_runtime_without_tag_yields_no_shape():
    assert runtime_shape(_cfg(BenchRuntime.image)) is None
    assert bench_service_specs(_cfg(BenchRuntime.image)) == ()


def test_deploy_tag_context_overrides_recorded_tag():
    cfg = _cfg(BenchRuntime.image, tag="repo:old")
    shape = runtime_shape(cfg, RenderContext(deploy_tag="repo:new"))
    assert shape.image("frappe") == "repo:new"


# ------------------------------------------------------------------- factory


def test_bench_specs_cover_code_services_with_rolling_roles():
    specs = {s.name: s for s in bench_service_specs(_cfg(BenchRuntime.image, tag="r:t"))}
    assert set(specs) == {"frappe", "nginx", "socketio", "schedule"}
    assert specs["frappe"].rolling
    assert specs["nginx"].rolling
    assert not specs["socketio"].rolling
    assert not specs["schedule"].rolling
    assert all(s.enabled for s in specs.values())


def test_worker_specs_and_bind_strings():
    (spec,) = worker_service_specs(_cfg(BenchRuntime.mount), ["long-worker"])
    assert spec.image is None
    assert bind_strings(spec) == ["./workspace:/workspace"]
    (ispec,) = worker_service_specs(_cfg(BenchRuntime.image, tag="r:t"), ["long-worker"])
    assert ispec.image == "r:t"
    assert "./workspace/frappe-bench/logs:/workspace/frappe-bench/logs" in bind_strings(ispec)


# ------------------------------------------------------------------ renderer

COMPOSE = """\
services:
  frappe:
    image: base:1
    volumes:
      - fm-sockets:/fm-sockets
      - ./workspace:/workspace
  nginx:
    image: nginx:1
    volumes:
      - ./configs/nginx/conf:/etc/nginx
      - ./workspace:/workspace
volumes:
  fm-sockets:
"""


def _cfm(tmp_path):
    p = tmp_path / "docker-compose.yml"
    p.write_text(COMPOSE)
    return ComposeFile(p)


def test_apply_specs_image_mode_projects_and_preserves(tmp_path):
    cfm = _cfm(tmp_path)
    cfg = _cfg(BenchRuntime.image, tag="ghcr.io/acme/erp:jun01")
    apply_specs(cfm, bench_service_specs(cfg), cfg.name)

    fr = [str(v) for v in cfm.get_service_volumes("frappe")]
    assert "fm-sockets:/fm-sockets" in fr  # named volume preserved
    assert not any(v.endswith(":/workspace") for v in fr)  # wholesale bind gone
    assert "./workspace/frappe-bench/logs:/workspace/frappe-bench/logs" in fr
    ng = [str(v) for v in cfm.get_service_volumes("nginx")]
    assert "./configs/nginx/conf:/etc/nginx" in ng  # nginx conf binds preserved
    assert cfm.yml["services"]["frappe"]["image"] == "ghcr.io/acme/erp:jun01"
    assert cfm.yml["services"]["nginx"]["image"] == "ghcr.io/acme/erp-nginx:jun01"


def test_apply_specs_idempotent_no_duplicates(tmp_path):
    cfm = _cfm(tmp_path)
    cfg = _cfg(BenchRuntime.image, tag="r:t1")
    apply_specs(cfm, bench_service_specs(cfg), cfg.name)
    apply_specs(cfm, bench_service_specs(cfg), cfg.name)  # re-render, same tag
    apply_specs(cfm, bench_service_specs(cfg, RenderContext(deploy_tag="r:t2")), cfg.name)  # re-pin
    raw = list(cfm.yml["services"]["frappe"]["volumes"])
    assert len(raw) == len(set(raw))  # never duplicates
    assert cfm.yml["services"]["frappe"]["image"] == "r:t2"


def test_apply_specs_mount_mode_round_trip(tmp_path):
    cfm = _cfm(tmp_path)
    cfg = _cfg(BenchRuntime.mount)
    apply_specs(cfm, bench_service_specs(cfg), cfg.name)
    fr = [str(v) for v in cfm.get_service_volumes("frappe")]
    assert "./workspace:/workspace" in fr  # mount keeps live workspace
    assert cfm.yml["services"]["frappe"]["image"] == "base:1"  # template default kept


def test_apply_specs_switch_image_to_mount_restores_workspace(tmp_path):
    cfm = _cfm(tmp_path)
    apply_specs(cfm, bench_service_specs(_cfg(BenchRuntime.image, tag="r:t")), "s.localhost")
    apply_specs(cfm, bench_service_specs(_cfg(BenchRuntime.mount)), "s.localhost")
    fr = [str(v) for v in cfm.get_service_volumes("frappe")]
    assert "./workspace:/workspace" in fr
    assert not any("/workspace/frappe-bench/logs" in v for v in fr)  # data binds stripped
